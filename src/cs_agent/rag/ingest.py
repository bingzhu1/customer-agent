"""把 `policies/*.yaml` 灌进 `agent.policy_chunks`（FR-301、PRD §11 ①②）。

```
uv run python -m cs_agent.rag.ingest            # 用 OpenAI 向量化
uv run python -m cs_agent.rag.ingest --fake     # 用 FakeEmbeddings，不触网
```

幂等：主键是 `(policy_id, policy_version, chunk_index)`，重跑走 `ON CONFLICT DO UPDATE`
覆盖内容与向量，不产生重复行。改了 YAML 重跑，库里的内容与版本随之变化（FR-301 验收标准）。

`prune=True`（默认）还会删掉"YAML 里已经没有的" chunk——包括被删掉的 FAQ 与**旧版本**。
理由：YAML 是唯一事实来源，库是它的投影；留着旧版本的 chunk 会让检索把过期条款召回来，
而引用—执行一致性校验（FR-306）比对的是当前版本，二者必然打架。

分层：本模块自己拿连接写 `agent` schema，属于 ingestion 批处理，不经过 Service / Repository；
它不读也不写任何 biz 表，不接触身份。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from cs_agent.db.base import get_engine
from cs_agent.policy.schema import load_policies
from cs_agent.rag.chunker import PolicyChunkData, chunk_policies
from cs_agent.rag.embeddings import (
    EmbeddingProvider,
    FakeEmbeddings,
    OpenAIEmbeddings,
    format_vector,
)

DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[3] / "policies"

_UPSERT = text(
    """
    INSERT INTO agent.policy_chunks
        (policy_id, policy_version, chunk_index, content, anchor, metadata, embedding)
    VALUES
        (:policy_id, :policy_version, :chunk_index, :content, :anchor,
         CAST(:metadata AS jsonb), CAST(:embedding AS vector))
    ON CONFLICT (policy_id, policy_version, chunk_index) DO UPDATE SET
        content   = EXCLUDED.content,
        anchor    = EXCLUDED.anchor,
        metadata  = EXCLUDED.metadata,
        embedding = EXCLUDED.embedding
    """
)

#: 删掉不再属于当前 YAML 的行。复合键拼成 `版本:序号` 文本再比对，
#: 因为 Postgres 的 `<> ALL(...)` 只接受一维数组，行值比不了。
_PRUNE_STALE_CHUNKS = text(
    """
    DELETE FROM agent.policy_chunks
    WHERE policy_id = :policy_id
      AND policy_version || ':' || chunk_index <> ALL(:keep)
    """
)

#: 整条策略从 YAML 里删掉时，它名下所有 chunk 一并清掉。
_PRUNE_STALE_POLICIES = text("DELETE FROM agent.policy_chunks WHERE policy_id <> ALL(:policy_ids)")


@dataclass(frozen=True, slots=True)
class IngestReport:
    policies: int
    chunks: int
    pruned: int
    total_rows: int

    def render(self) -> str:
        return (
            f"policies={self.policies} chunks={self.chunks} "
            f"pruned={self.pruned} policy_chunks_rows={self.total_rows}"
        )


def _prune_stale(conn: Connection, chunks: Sequence[PolicyChunkData]) -> int:
    """按 policy_id 分组，删掉本次没生成的 (version, chunk_index)。返回删除行数。"""
    keep: dict[str, list[tuple[int, int]]] = {}
    for c in chunks:
        keep.setdefault(c.policy_id, []).append((c.policy_version, c.chunk_index))
    if not keep:
        return 0
    pruned = 0
    for policy_id, pairs in sorted(keep.items()):
        alive = [f"{version}:{index}" for version, index in sorted(pairs)]
        pruned += conn.execute(
            _PRUNE_STALE_CHUNKS, {"policy_id": policy_id, "keep": alive}
        ).rowcount
    pruned += conn.execute(_PRUNE_STALE_POLICIES, {"policy_ids": sorted(keep)}).rowcount
    return pruned


def ingest_chunks(
    chunks: Sequence[PolicyChunkData],
    provider: EmbeddingProvider,
    *,
    engine: Engine | None = None,
    prune: bool = True,
) -> IngestReport:
    """向量化并写库。整批在一个事务里，失败则全部回滚，不留半套语料。"""
    vectors = provider.embed([c.content for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(f"向量条数 {len(vectors)} 与 chunk 数 {len(chunks)} 不一致")

    engine = engine or get_engine()
    with engine.begin() as conn:
        for chunk, vector in zip(chunks, vectors, strict=True):
            if len(vector) != provider.dimensions:
                raise RuntimeError(f"{chunk.policy_id}#{chunk.chunk_index}: 向量维度不符")
            conn.execute(
                _UPSERT,
                {
                    "policy_id": chunk.policy_id,
                    "policy_version": chunk.policy_version,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "anchor": chunk.anchor,
                    "metadata": chunk.metadata_json(),
                    "embedding": format_vector(vector),
                },
            )
        pruned = _prune_stale(conn, chunks) if prune else 0
        total = conn.execute(text("SELECT count(*) FROM agent.policy_chunks")).scalar_one()

    return IngestReport(
        policies=len({c.policy_id for c in chunks}),
        chunks=len(chunks),
        pruned=pruned,
        total_rows=int(total),
    )


def ingest_policies(
    directory: Path | None = None,
    *,
    provider: EmbeddingProvider | None = None,
    engine: Engine | None = None,
    prune: bool = True,
) -> IngestReport:
    policies = load_policies(directory or DEFAULT_POLICY_DIR)
    chunks = chunk_policies(policies)
    return ingest_chunks(chunks, provider or OpenAIEmbeddings(), engine=engine, prune=prune)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把策略 YAML 灌入 agent.policy_chunks")
    parser.add_argument("--policies", type=Path, default=DEFAULT_POLICY_DIR, help="策略目录")
    parser.add_argument("--fake", action="store_true", help="用 FakeEmbeddings，不调用 OpenAI")
    parser.add_argument("--no-prune", action="store_true", help="保留 YAML 中已不存在的旧 chunk")
    args = parser.parse_args(argv)

    provider: EmbeddingProvider = FakeEmbeddings() if args.fake else OpenAIEmbeddings()
    report = ingest_policies(args.policies, provider=provider, prune=not args.no_prune)
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
