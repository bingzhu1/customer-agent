"""τ_low / τ_high 标定（FR-309、PRD §11.1、ADR-0007）。

```
uv run python scripts/calibrate_tau.py                   # FakeEmbeddings，不触网，验证脚本
uv run python scripts/calibrate_tau.py --real            # 真调 OpenAI，产出 ADR-0007 的分布表
uv run python scripts/calibrate_tau.py --real --rewrite  # 再叠加 FR-302 查询改写
```

做法照搬 PRD §11.1：
1. 取 golden 里 `category: rag` 的用例，每条的首轮用户话术作为查询；
2. 用**同一个 provider** 先 ingest 再检索（向量空间必须一致，所以脚本自己先灌一遍库）；
3. 按用例的期望把它标成正/负样本：
   - 负样本 = 期望 `RETRIEVAL_NO_RESULT`（政策未覆盖，检索本就不该有答案）
   - 正样本 = 期望正常回答（`confidence: normal`）
   - 低置信样本 = 期望 `RETRIEVAL_LOW_CONFIDENCE`，按定义落在中间带，两端分位都不该由它决定
   - 陷阱样本 = 绑定了具体订单的资格判定题（RAG-009/010），走的是矩阵规则 10.5 而非检索分带，
     只列出来看，不参与分位计算
4. τ_low 取负样本 95 分位，τ_high 取正样本 5 分位。

`--rewrite` 会先用 `rag.rewrite.rewrite_query` 把用例里的口语问题改写成检索 query（FR-302），
再拿改写后的 query 去检索。两次跑（带 / 不带 `--rewrite`）的分布放在一起看，
才知道"分数重叠"是检索本身不行，还是查询没写对。
golden 用例是单轮的，没有前序工具结果，因此 `CaseFacts` 传空——改写全靠句子本身。

脚本**只输出建议值，不改 `settings.py`**——阈值是要写进 ADR 并由人拍板的。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from cs_agent.domain.enums import GoldenCategory, ReasonCode  # noqa: E402
from cs_agent.eval.schema import GoldenCase, load_golden  # noqa: E402
from cs_agent.memory.case_facts import CaseFacts  # noqa: E402
from cs_agent.rag.embeddings import (  # noqa: E402
    EmbeddingProvider,
    FakeEmbeddings,
    OpenAIEmbeddings,
)
from cs_agent.rag.ingest import ingest_policies  # noqa: E402
from cs_agent.rag.retriever import PolicyRetriever, classify_band  # noqa: E402
from cs_agent.rag.rewrite import RewrittenQuery, rewrite_query  # noqa: E402
from cs_agent.settings import get_settings  # noqa: E402

Label = Literal["positive", "low", "negative", "trap"]

GOLDEN_DIR = REPO_ROOT / "data" / "golden"
POLICY_DIR = REPO_ROOT / "policies"

LABEL_TEXT: dict[Label, str] = {
    "positive": "正样本",
    "low": "低置信",
    "negative": "负样本",
    "trap": "陷阱（不参与分位）",
}


@dataclass(frozen=True, slots=True)
class Row:
    case_id: str
    label: Label
    query: str
    #: 实际送去检索的 query。不开 --rewrite 时与 `query` 相同。
    search_query: str
    max_score: float
    top_policy_id: str
    band: str


def label_of(case: GoldenCase) -> Label:
    """按 golden 的期望反推样本类型。判据只用 expect，不看 notes，避免主观。"""
    codes = set(case.expect.reason_code_any_of)
    if case.expect.reason_code is not None:
        codes.add(case.expect.reason_code)
    if ReasonCode.RETRIEVAL_NO_RESULT in codes:
        return "negative"
    if ReasonCode.LOW_CONFIDENCE_ON_DECISION in codes:
        return "trap"
    if ReasonCode.RETRIEVAL_LOW_CONFIDENCE in codes:
        return "low"
    return "positive"


def percentile(values: list[float], q: float) -> float:
    """线性插值分位数。样本量只有个位数，不引入 numpy。"""
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def collect(provider: EmbeddingProvider, top_k: int, *, rewrite: bool = False) -> list[Row]:
    cases = [c for c in load_golden(GOLDEN_DIR).cases if c.category is GoldenCategory.RAG]
    settings = get_settings()
    # τ 给一对不影响检索的哨兵值：这一步只要分数，分带用当前配置另算
    retriever = PolicyRetriever(provider, tau_low=0.0, tau_high=1.1, top_k=top_k)
    rows: list[Row] = []
    for case in sorted(cases, key=lambda c: c.id):
        query = next((t.user for t in case.turns if t.user), "")
        search_query = query
        if rewrite:
            # golden 是单轮用例，没有前序工具结果，CaseFacts 只能是空的
            rewritten: RewrittenQuery = rewrite_query(query, CaseFacts())
            search_query = rewritten.query
        result = retriever.search(search_query)
        rows.append(
            Row(
                case_id=case.id,
                label=label_of(case),
                query=query,
                search_query=search_query,
                max_score=result.max_score,
                top_policy_id=result.chunks[0].policy_id if result.chunks else "-",
                band=classify_band(result.max_score, settings.rag_tau_low, settings.rag_tau_high),
            )
        )
    return rows


def render(rows: list[Row], provider_name: str, top_k: int, *, rewrite: bool = False) -> str:
    settings = get_settings()
    mode = "查询改写：开（FR-302）" if rewrite else "查询改写：关（原句直接检索）"
    out = [
        f"# τ 标定分布（provider={provider_name}, top_k={top_k}, 用例={len(rows)}）",
        "",
        mode,
        "",
        f"当前配置：τ_low={settings.rag_tau_low} τ_high={settings.rag_tau_high}"
        "（下表 `当前分带` 按此计算）",
        "",
        "| 用例 | 样本类型 | max_score | top policy | 当前分带 | 送检索的 query |",
        "|---|---|---:|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| {r.case_id} | {LABEL_TEXT[r.label]} | {r.max_score:.4f} | "
            f"{r.top_policy_id} | {r.band} | {r.search_query} |"
        )

    groups: dict[Label, list[float]] = {}
    for r in rows:
        groups.setdefault(r.label, []).append(r.max_score)

    out += [
        "",
        "## 分组统计",
        "",
        "| 样本类型 | n | min | p05 | p50 | p95 | max |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label in ("positive", "low", "negative", "trap"):
        vals = groups.get(label, [])  # type: ignore[arg-type]
        if not vals:
            continue
        out.append(
            f"| {LABEL_TEXT[label]} | {len(vals)} | {min(vals):.4f} | "  # type: ignore[index]
            f"{percentile(vals, 0.05):.4f} | {percentile(vals, 0.50):.4f} | "
            f"{percentile(vals, 0.95):.4f} | {max(vals):.4f} |"
        )

    negatives = groups.get("negative", [])
    positives = groups.get("positive", [])
    tau_low = percentile(negatives, 0.95)
    tau_high = percentile(positives, 0.05)
    out += [
        "",
        "## 建议值（PRD §11.1：τ_low = 负样本 p95，τ_high = 正样本 p05）",
        "",
        f"- 建议 `rag_tau_low`  = **{tau_low:.4f}**（负样本 n={len(negatives)}）",
        f"- 建议 `rag_tau_high` = **{tau_high:.4f}**（正样本 n={len(positives)}）",
    ]
    if tau_low >= tau_high:
        out.append(
            "- ⚠️ 负样本 p95 已经不低于正样本 p05：两类分数重叠，说明检索质量不足以划出干净的带，"
            "不要直接采用，先改进检索（查询改写 / hybrid）再标定。"
        )
    low_scores = groups.get("low", [])
    if low_scores:
        inside = [s for s in low_scores if tau_low <= s < tau_high]
        out.append(f"- 低置信用例落在建议带内的：{len(inside)}/{len(low_scores)}")
    out += [
        "",
        "> 本脚本只给建议值，不改 `settings.py`；最终取值由人拍板并写进 ADR-0007 附录。",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用 golden 的 rag 用例标定 τ_low / τ_high")
    parser.add_argument("--real", action="store_true", help="真调 OpenAI（默认用 FakeEmbeddings）")
    parser.add_argument("--rewrite", action="store_true", help="先跑 FR-302 查询改写再检索")
    parser.add_argument("--top-k", type=int, default=get_settings().rag_top_k)
    parser.add_argument("--out", type=Path, default=None, help="把 markdown 写到文件")
    parser.add_argument(
        "--no-ingest", action="store_true", help="跳过重新灌库（确认向量已一致时用）"
    )
    args = parser.parse_args(argv)

    provider: EmbeddingProvider = OpenAIEmbeddings() if args.real else FakeEmbeddings()
    name = "openai:" + get_settings().embedding_model if args.real else "fake"
    if not args.no_ingest:
        # 检索必须用与入库相同的向量空间，所以先按所选 provider 重灌一遍
        print(
            f"[ingest] {ingest_policies(POLICY_DIR, provider=provider).render()}", file=sys.stderr
        )

    report = render(
        collect(provider, args.top_k, rewrite=args.rewrite), name, args.top_k, rewrite=args.rewrite
    )
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"written: {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
