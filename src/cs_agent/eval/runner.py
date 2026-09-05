"""eval runner：驱动 golden 用例跑被测 agent，收集每轮结果与断言。

不走 HTTP，直接调用 `AgentUnderTest`（PRD §15 Phase 0）。
跨轮特判（schema 表达不了的两类）：
- category=idempotency：整个用例 biz.refunds 净增必须恰好 1；
  并发确认时恰好一次非 IDEMPOTENT_REPLAY；
- tag=existence-leak：各轮 DENY 回复去掉数字后必须逐字一致（不泄露存在性）。
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.assertions import Check, CheckList, check_turn
from cs_agent.eval.judge import Judge, JudgeResult
from cs_agent.eval.protocol import AgentSession, AgentUnderTest, TurnResult, Usage
from cs_agent.eval.schema import GoldenCase, GoldenDataset, Turn
from cs_agent.eval.side_effects import SideEffectKind, SideEffectProbe
from cs_agent.seed.reference import EVAL_NOW

EXISTENCE_LEAK_TAG = "existence-leak"


@dataclass
class TurnRecord:
    index: int
    kind: str  # "user" | "confirm"
    input_text: str | None
    results: list[TurnResult]
    effects: set[SideEffectKind]
    checks: CheckList
    latency_ms: float

    @property
    def representative(self) -> TurnResult:
        """并发确认时优先取 IDEMPOTENT_REPLAY 那一次（golden README 约定），否则取最后一次。"""
        for r in self.results:
            if r.reason_code is ReasonCode.IDEMPOTENT_REPLAY:
                return r
        return self.results[-1]


@dataclass
class CaseResult:
    case: GoldenCase
    turns: list[TurnRecord] = field(default_factory=list)
    cross_checks: CheckList = field(default_factory=CheckList)
    error: str | None = None
    judge: JudgeResult | None = None
    total_effects: set[SideEffectKind] = field(default_factory=set)

    @property
    def final_result(self) -> TurnResult | None:
        return self.turns[-1].representative if self.turns else None

    @property
    def all_checks(self) -> list[Check]:
        out: list[Check] = []
        for t in self.turns:
            out.extend(t.checks.checks)
        out.extend(self.cross_checks.checks)
        return out

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.all_checks)

    @property
    def usage(self) -> Usage:
        total = Usage()
        for t in self.turns:
            for r in t.results:
                total = total + r.usage
        return total

    @property
    def latency_ms(self) -> float:
        return sum(t.latency_ms for t in self.turns)

    @property
    def tool_call_count(self) -> int:
        return sum(len(r.tool_calls) for t in self.turns for r in t.results)

    def failed_checks(self) -> list[Check]:
        return [c for c in self.all_checks if not c.passed]

    def metrics_dict(self) -> dict[str, Any]:
        u = self.usage
        return {
            "latency_ms": round(self.latency_ms, 1),
            "llm_calls": u.llm_calls,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_input_tokens": u.cache_read_input_tokens,
            "tool_calls": self.tool_call_count,
            "turns": len(self.turns),
            "failed_checks": [c.name for c in self.failed_checks()],
        }

    def raw_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "judge": None if self.judge is None else self.judge.__dict__,
            "turns": [
                {
                    "index": t.index,
                    "kind": t.kind,
                    "input": t.input_text,
                    "effects": sorted(t.effects),
                    "results": [r.model_dump(mode="json") for r in t.results],
                    "checks": [c.__dict__ for c in t.checks.checks],
                }
                for t in self.turns
            ],
            "cross_checks": [c.__dict__ for c in self.cross_checks.checks],
        }


@dataclass
class RunResult:
    agent_name: str
    started_at: datetime
    finished_at: datetime
    cases: list[CaseResult]
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)


ProgressHook = Callable[[CaseResult], None]


def _run_confirm(session: AgentSession, turn: Turn) -> list[TurnResult]:
    if turn.concurrent and turn.repeat > 1:
        with ThreadPoolExecutor(max_workers=turn.repeat) as pool:
            futures = [pool.submit(session.confirm) for _ in range(turn.repeat)]
            return [f.result() for f in futures]
    return [session.confirm() for _ in range(turn.repeat)]


def _cross_checks(case: GoldenCase, cr: CaseResult) -> None:
    if case.category.value == "idempotency":
        refunds = sum(
            1 for t in cr.turns if "refund_created" in t.effects
        )  # 每轮最多记一次；精确净增由探针快照给出
        cr.cross_checks.add(
            "refund_exactly_once",
            refunds == 1 and "refund_created" in cr.total_effects,
            f"refund_created observed in {refunds} turn(s)",
        )
        for t in cr.turns:
            if t.kind == "confirm" and len(t.results) > 1:
                executed = [
                    r for r in t.results if r.reason_code is not ReasonCode.IDEMPOTENT_REPLAY
                ]
                cr.cross_checks.add(
                    "exactly_one_executed",
                    len(executed) == 1,
                    f"{len(executed)} of {len(t.results)} confirms executed (expected 1)",
                )
    user_turns = sum(1 for t in cr.turns if t.kind == "user")
    if EXISTENCE_LEAK_TAG in case.tags and user_turns >= 2:
        # 单轮用例（如 ORD-005）无法在用例内比较模板，一致性由多轮用例（SEC-010）覆盖
        denies = [
            t.representative.reply
            for t in cr.turns
            if t.kind == "user" and t.representative.decision is DecisionOutcome.DENY
        ]
        normalized = {"".join(ch for ch in r if not ch.isdigit()).strip() for r in denies}
        cr.cross_checks.add(
            "existence_leak_template_consistent",
            len(denies) >= 2 and len(normalized) == 1,
            f"{len(denies)} DENY replies, {len(normalized)} distinct templates",
        )


def run_case(
    agent: AgentUnderTest,
    case: GoldenCase,
    probe: SideEffectProbe,
    *,
    now: datetime = EVAL_NOW,
    judge: Judge | None = None,
) -> CaseResult:
    cr = CaseResult(case=case)
    before_case = probe.snapshot()
    session: AgentSession | None = None
    try:
        session = agent.start_session(case.auth, now=now)
        last_index = len(case.turns) - 1
        for i, turn in enumerate(case.turns):
            before = probe.snapshot()
            t0 = time.perf_counter()
            if turn.user is not None:
                results = [session.send_user(turn.user, faults=turn.faults or None)]
                kind, text = "user", turn.user
            else:
                results = _run_confirm(session, turn)
                kind, text = "confirm", None
            latency = (time.perf_counter() - t0) * 1000
            effects = before.diff(probe.snapshot())
            record = TurnRecord(i, kind, text, results, effects, CheckList(), latency)
            rep = record.representative
            if i == last_index:
                record.checks = check_turn(case.expect, rep, effects)
                if turn.expect is not None:
                    # 最后一轮同时写了轮级 expect：追加其断言，不变式已在上面查过
                    extra = check_turn(turn.expect, rep, effects).checks
                    seen = {c.name for c in record.checks.checks}
                    record.checks.checks.extend(c for c in extra if c.name not in seen)
            else:
                record.checks = check_turn(turn.expect, rep, effects)
            cr.turns.append(record)
        cr.total_effects = before_case.diff(probe.snapshot())
        _cross_checks(case, cr)
        if judge is not None and cr.final_result is not None:
            cr.judge = judge.judge(case, cr.final_result)
    except Exception:  # noqa: BLE001  被测方任何异常都记为该用例失败，不中断整批
        cr.error = traceback.format_exc(limit=8)
    finally:
        if session is not None:
            session.close()
    return cr


def run_dataset(
    agent: AgentUnderTest,
    dataset: GoldenDataset,
    probe: SideEffectProbe,
    *,
    now: datetime = EVAL_NOW,
    judge: Judge | None = None,
    case_filter: Callable[[GoldenCase], bool] | None = None,
    on_case: ProgressHook | None = None,
    config: dict[str, Any] | None = None,
) -> RunResult:
    started = datetime.now(UTC)
    selected: Iterable[GoldenCase] = (
        c for c in dataset.cases if case_filter is None or case_filter(c)
    )
    results: list[CaseResult] = []
    for case in selected:
        cr = run_case(agent, case, probe, now=now, judge=judge)
        results.append(cr)
        if on_case is not None:
            on_case(cr)
    return RunResult(
        agent_name=agent.name,
        started_at=started,
        finished_at=datetime.now(UTC),
        cases=results,
        config={"now": now.isoformat(), "judge": judge is not None, **(config or {})},
    )
