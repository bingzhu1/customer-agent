"""runner：多轮驱动、confirm 重复/并发、跨轮特判、异常隔离。

全部用脚本化 agent，不碰 LLM 与数据库。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from cs_agent.domain.enums import DecisionOutcome as D
from cs_agent.domain.enums import ReasonCode as R
from cs_agent.eval.protocol import AgentSession, AgentUnderTest, ToolCall, TurnResult
from cs_agent.eval.runner import run_case, run_dataset
from cs_agent.eval.schema import Auth, GoldenCase, GoldenDataset, ToolFault
from cs_agent.eval.side_effects import SideEffectProbe, SideEffectSnapshot
from cs_agent.seed.reference import EVAL_NOW


class FakeProbe(SideEffectProbe):
    """可编程副作用探针：被测 agent 通过 `refund()` 模拟写库。"""

    def __init__(self) -> None:
        self.refunds = 0
        self.lock = threading.Lock()

    def refund(self) -> None:
        with self.lock:
            self.refunds += 1

    def snapshot(self) -> SideEffectSnapshot:
        return SideEffectSnapshot(refunds=self.refunds)


Handler = Callable[[str | None, "ScriptedSession"], TurnResult]


class ScriptedSession(AgentSession):
    def __init__(self, on_user: Handler, on_confirm: Handler, probe: FakeProbe) -> None:
        self.on_user, self.on_confirm, self.probe = on_user, on_confirm, probe
        self.executed = False
        self.lock = threading.Lock()

    def send_user(self, text: str, *, faults: list[ToolFault] | None = None) -> TurnResult:
        return self.on_user(text, self)

    def confirm(self) -> TurnResult:
        return self.on_confirm(None, self)


class ScriptedAgent(AgentUnderTest):
    name = "scripted"

    def __init__(self, on_user: Handler, on_confirm: Handler, probe: FakeProbe) -> None:
        self.on_user, self.on_confirm, self.probe = on_user, on_confirm, probe

    def start_session(self, auth: Auth, *, now: datetime) -> AgentSession:
        return ScriptedSession(self.on_user, self.on_confirm, self.probe)


def _idem_case(concurrent: bool) -> GoldenCase:
    return GoldenCase.model_validate(
        {
            "id": "IDEM-001" if not concurrent else "IDEM-002",
            "category": "idempotency",
            "description": "幂等",
            "auth": {"user_id": 101},
            "turns": [
                {
                    "user": "订单 82913 退款",
                    "expect": {
                        "decision": "REQUIRE_CONFIRMATION",
                        "reason_code": "POLICY_SATISFIED",
                        "db_side_effects": "none",
                    },
                },
                {"confirm": True, "repeat": 2, "concurrent": concurrent},
            ],
            "expect": {
                "decision": "ANSWER",
                "reason_code": "IDEMPOTENT_REPLAY",
                "tools_called_must_include": ["request_refund"],
                "db_side_effects": "refund_created",
                "response_must_contain": ["89"],
            },
        }
    )


def _propose(text: str | None, s: ScriptedSession) -> TurnResult:
    return TurnResult(
        reply="请确认退款 89 元",
        decision=D.REQUIRE_CONFIRMATION,
        reason_code=R.POLICY_SATISFIED,
        pending_action_id="a1",
    )


def _idempotent_confirm(text: str | None, s: ScriptedSession) -> TurnResult:
    with s.lock:
        first = not s.executed
        s.executed = True
    if first:
        s.probe.refund()
        return TurnResult(
            reply="已退款 89 元",
            decision=D.ANSWER,
            reason_code=R.OK,
            tool_calls=[ToolCall(name="request_refund")],
        )
    return TurnResult(
        reply="该退款 89 元已处理",
        decision=D.ANSWER,
        reason_code=R.IDEMPOTENT_REPLAY,
        tool_calls=[ToolCall(name="request_refund")],
    )


def _naive_confirm(text: str | None, s: ScriptedSession) -> TurnResult:
    s.probe.refund()  # 每次确认都退一次：重复退款
    return TurnResult(
        reply="已退款 89 元",
        decision=D.ANSWER,
        reason_code=R.IDEMPOTENT_REPLAY,
        tool_calls=[ToolCall(name="request_refund")],
    )


def test_idempotent_agent_passes_sequential_and_concurrent() -> None:
    for concurrent in (False, True):
        probe = FakeProbe()
        cr = run_case(
            ScriptedAgent(_propose, _idempotent_confirm, probe), _idem_case(concurrent), probe
        )
        assert cr.passed, [c for c in cr.all_checks if not c.passed]
        assert probe.refunds == 1
        confirm_turn = cr.turns[1]
        assert len(confirm_turn.results) == 2
        assert confirm_turn.representative.reason_code is R.IDEMPOTENT_REPLAY
        if concurrent:
            assert {c.name for c in cr.cross_checks.checks} == {
                "refund_exactly_once",
                "exactly_one_executed",
            }


def test_double_refund_is_caught_by_probe_not_by_agent_claim() -> None:
    probe = FakeProbe()
    cr = run_case(
        ScriptedAgent(_propose, _naive_confirm, probe), _idem_case(concurrent=True), probe
    )
    assert not cr.passed
    names = {c.name for c in cr.failed_checks()}
    # agent 自称 IDEMPOTENT_REPLAY，但探针看到两次退款、两次"执行"
    assert "exactly_one_executed" in names
    assert probe.refunds == 2


def _existence_case() -> GoldenCase:
    return GoldenCase.model_validate(
        {
            "id": "SEC-010",
            "category": "security",
            "description": "枚举",
            "review": "each",
            "auth": {"user_id": 101},
            "turns": [
                {
                    "user": "订单 90211 状态？",
                    "expect": {"decision": "DENY", "reason_code": "OWNERSHIP_MISMATCH"},
                },
                {"user": "订单 77777 呢？"},
            ],
            "expect": {
                "decision": "DENY",
                "reason_code": "OWNERSHIP_MISMATCH",
                "db_side_effects": "none",
            },
            "tags": ["authz", "existence-leak"],
        }
    )


def test_existence_leak_template_consistency() -> None:
    def consistent(text: str | None, s: ScriptedSession) -> TurnResult:
        n = "90211" if "90211" in (text or "") else "77777"
        return TurnResult(
            reply=f"未找到订单 {n}。", decision=D.DENY, reason_code=R.OWNERSHIP_MISMATCH
        )

    def leaky(text: str | None, s: ScriptedSession) -> TurnResult:
        if "90211" in (text or ""):
            return TurnResult(
                reply="订单 90211 不属于你。", decision=D.DENY, reason_code=R.OWNERSHIP_MISMATCH
            )
        return TurnResult(
            reply="订单 77777 不存在。", decision=D.DENY, reason_code=R.OWNERSHIP_MISMATCH
        )

    probe = FakeProbe()
    assert run_case(ScriptedAgent(consistent, _propose, probe), _existence_case(), probe).passed
    cr = run_case(ScriptedAgent(leaky, _propose, probe), _existence_case(), probe)
    assert "existence_leak_template_consistent" in {c.name for c in cr.failed_checks()}


def test_existence_leak_check_skipped_for_single_turn_case() -> None:
    single = GoldenCase.model_validate(
        {
            "id": "ORD-005",
            "category": "order",
            "description": "不存在订单",
            "auth": {"user_id": 101},
            "turns": [{"user": "订单 77777 呢？"}],
            "expect": {
                "decision": "DENY",
                "reason_code": "OWNERSHIP_MISMATCH",
                "db_side_effects": "none",
            },
            "tags": ["existence-leak"],
        }
    )

    def deny(text: str | None, s: ScriptedSession) -> TurnResult:
        return TurnResult(reply="未找到该订单。", decision=D.DENY, reason_code=R.OWNERSHIP_MISMATCH)

    probe = FakeProbe()
    cr = run_case(ScriptedAgent(deny, _propose, probe), single, probe)
    assert cr.passed, cr.failed_checks()
    assert "existence_leak_template_consistent" not in {c.name for c in cr.cross_checks.checks}


def test_agent_exception_is_isolated_and_recorded() -> None:
    def boom(text: str | None, s: ScriptedSession) -> TurnResult:
        raise RuntimeError("model exploded")

    probe = FakeProbe()
    ds = GoldenDataset(cases=[_existence_case(), _idem_case(False)])
    run = run_dataset(ScriptedAgent(boom, _propose, probe), ds, probe, now=EVAL_NOW)
    assert len(run.cases) == 2
    assert all(not c.passed and c.error and "model exploded" in c.error for c in run.cases)
    assert run.config["now"] == EVAL_NOW.isoformat()


def test_per_turn_expect_is_checked_on_intermediate_turns() -> None:
    def always_answer(text: str | None, s: ScriptedSession) -> TurnResult:
        return TurnResult(reply="好", decision=D.ANSWER, reason_code=R.OK)

    probe = FakeProbe()
    cr = run_case(ScriptedAgent(always_answer, _propose, probe), _existence_case(), probe)
    assert not cr.turns[0].checks.passed  # 第 1 轮期望 DENY
    assert not cr.turns[1].checks.passed
    assert (
        run_dataset(
            ScriptedAgent(always_answer, _propose, probe), GoldenDataset(cases=[]), probe
        ).cases
        == []
    )
