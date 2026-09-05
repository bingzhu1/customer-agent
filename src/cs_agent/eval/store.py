"""把一次评估写入 agent.eval_runs / agent.eval_results（FR-904）。"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cs_agent.db.models.agent import EvalResult, EvalRun
from cs_agent.eval.runner import RunResult


def persist_run(engine: Engine, run: RunResult, git_sha: str | None) -> int:
    with Session(engine) as session:
        row = EvalRun(
            version_tag=run.agent_name,
            git_sha=git_sha,
            started_at=run.started_at,
            finished_at=run.finished_at,
            config=run.config,
        )
        session.add(row)
        session.flush()
        for cr in run.cases:
            final = cr.final_result
            session.add(
                EvalResult(
                    run_id=row.id,
                    case_id=cr.case.id,
                    passed=cr.passed,
                    decision=final.decision.value if final else None,
                    reason_code=final.reason_code.value if final else None,
                    metrics=cr.metrics_dict(),
                    raw=cr.raw_dict(),
                )
            )
        session.commit()
        return int(row.id)
