"""`make eval` 入口：python -m cs_agent.eval --agent v0

跑完全量 golden，输出 markdown 报表到 eval_reports/，并写 agent.eval_runs / eval_results。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from cs_agent.eval.judge import LlmJudge
from cs_agent.eval.metrics import compute_metrics
from cs_agent.eval.registry import available_agents, build_agent
from cs_agent.eval.report import write_report
from cs_agent.eval.runner import CaseResult, run_dataset
from cs_agent.eval.schema import GoldenCase, load_golden
from cs_agent.eval.side_effects import DbSideEffectProbe, NullSideEffectProbe, SideEffectProbe
from cs_agent.policy.schema import load_policies
from cs_agent.seed.reference import EVAL_NOW
from cs_agent.settings import get_settings


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cs_agent.eval", description="跑 golden dataset 评估")
    p.add_argument("--agent", default="dummy", help=f"被测 agent：{available_agents()}")
    p.add_argument("--golden", type=Path, default=Path("data/golden"))
    p.add_argument("--policies", type=Path, default=Path("policies"))
    p.add_argument("--out", type=Path, default=Path("eval_reports"))
    p.add_argument("--filter", default="", help="只跑 id 或 category 含该子串的用例")
    p.add_argument("--judge", action="store_true", help="开启 LLM judge（语气 / groundedness）")
    p.add_argument("--no-db", action="store_true", help="不连数据库：副作用探针为空，也不落库")
    p.add_argument("--now", type=datetime.fromisoformat, default=EVAL_NOW, help="评估时钟（ISO）")
    p.add_argument("--strict", action="store_true", help="硬门槛未通过时以非零退出码结束")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    dataset = load_golden(args.golden)
    agent = build_agent(args.agent)

    probe: SideEffectProbe
    engine = None
    if args.no_db:
        probe = NullSideEffectProbe()
    else:
        from cs_agent.db.base import get_engine

        engine = get_engine()
        probe = DbSideEffectProbe(engine)

    judge = LlmJudge(load_policies(args.policies)) if args.judge else None

    def selected(case: GoldenCase) -> bool:
        return not args.filter or args.filter in case.id or args.filter in case.category.value

    def progress(cr: CaseResult) -> None:
        if not args.quiet:
            mark = "PASS" if cr.passed else ("ERR " if cr.error else "FAIL")
            print(f"[{mark}] {cr.case.id}  {cr.case.description}", file=sys.stderr)

    run = run_dataset(
        agent,
        dataset,
        probe,
        now=args.now,
        judge=judge,
        case_filter=selected,
        on_case=progress,
        config={
            "filter": args.filter,
            "no_db": args.no_db,
            "model_primary": settings.llm_model_primary,
        },
    )
    metrics = compute_metrics(run.cases, settings.llm_model_primary)
    sha = _git_sha()
    md_path, json_path = write_report(run, metrics, sha, args.out)

    run_id: int | None = None
    if engine is not None:
        from cs_agent.eval.store import persist_run

        run_id = persist_run(engine, run, sha)

    print(
        f"\n{run.agent_name}: {run.passed_count}/{len(run.cases)} passed; "
        f"hard gates {'PASS' if metrics.hard_gates_passed else 'FAIL'}"
    )
    print(
        f"report: {md_path}\njson:   {json_path}" + (f"\neval_run_id: {run_id}" if run_id else "")
    )
    if args.strict and not metrics.hard_gates_passed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
