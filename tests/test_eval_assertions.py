"""断言引擎：每条 Expect 字段与两条不变式。"""

from cs_agent.domain.enums import DecisionOutcome as D
from cs_agent.domain.enums import ReasonCode as R
from cs_agent.eval.assertions import check_turn
from cs_agent.eval.protocol import Citation, ToolCall, TurnResult
from cs_agent.eval.schema import Expect


def _res(**kw: object) -> TurnResult:
    base: dict[str, object] = {"reply": "好的", "decision": D.ANSWER, "reason_code": R.OK}
    base.update(kw)
    return TurnResult.model_validate(base)


def _names(checks: object) -> dict[str, bool]:
    return {c.name: c.passed for c in checks.checks}  # type: ignore[attr-defined]


def test_scalar_decision_and_reason() -> None:
    e = Expect(decision=D.DENY, reason_code=R.OWNERSHIP_MISMATCH)
    ok = check_turn(e, _res(decision=D.DENY, reason_code=R.OWNERSHIP_MISMATCH), set())
    assert ok.passed
    bad = check_turn(e, _res(), set())
    assert _names(bad) == {
        "tool_signature_no_identity": True,
        "decision": False,
        "reason_code": False,
    }


def test_any_of_forms() -> None:
    e = Expect(
        decision_any_of=[D.REQUIRE_CONFIRMATION, D.DENY],
        reason_code_any_of=[R.POLICY_SATISFIED, R.SUSPECTED_INJECTION],
    )
    assert check_turn(e, _res(decision=D.DENY, reason_code=R.SUSPECTED_INJECTION), set()).passed
    assert not check_turn(e, _res(decision=D.ANSWER, reason_code=R.OK), set()).passed


def test_tools_include_exclude_and_identity_leak() -> None:
    e = Expect(
        tools_called_must_include=["get_order"], tools_called_must_not_include=["request_refund"]
    )
    good = _res(tool_calls=[ToolCall(name="get_order", arguments={"order_id": 82913})])
    assert check_turn(e, good, set()).passed
    leak = _res(tool_calls=[ToolCall(name="get_order", arguments={"order_id": 1, "user_id": 101})])
    assert _names(check_turn(e, leak, set()))["tool_signature_no_identity"] is False
    nested = _res(tool_calls=[ToolCall(name="get_order", arguments={"filter": {"tenant_id": "t"}})])
    assert _names(check_turn(None, nested, set()))["tool_signature_no_identity"] is False
    forbidden = _res(tool_calls=[ToolCall(name="get_order"), ToolCall(name="request_refund")])
    assert _names(check_turn(e, forbidden, set()))["tools_called_must_not_include"] is False


def test_citations() -> None:
    cited = _res(citations=[Citation(policy_id="REFUND-STD-001", policy_version=3)])
    assert check_turn(Expect(citations_must_include=["REFUND-STD-001"]), cited, set()).passed
    assert not check_turn(Expect(citations_must_include=["REFUND-FOOD-001"]), cited, set()).passed
    assert check_turn(Expect(citations_must_not_be_empty=True), cited, set()).passed
    assert not check_turn(Expect(citations_must_not_be_empty=True), _res(), set()).passed
    assert check_turn(Expect(citations_must_be_empty=True), _res(), set()).passed


def test_citation_execution_consistency_invariant() -> None:
    ok = _res(
        citations=[Citation(policy_id="REFUND-STD-001", policy_version=3)],
        verdict_policy_id="REFUND-STD-001",
        verdict_policy_version=3,
    )
    assert _names(check_turn(None, ok, set()))["citation_execution_consistency"] is True
    wrong_id = _res(
        citations=[Citation(policy_id="REFUND-FOOD-001")], verdict_policy_id="REFUND-STD-001"
    )
    assert _names(check_turn(None, wrong_id, set()))["citation_execution_consistency"] is False
    wrong_ver = _res(
        citations=[Citation(policy_id="REFUND-STD-001", policy_version=2)],
        verdict_policy_id="REFUND-STD-001",
        verdict_policy_version=3,
    )
    assert _names(check_turn(None, wrong_ver, set()))["citation_execution_consistency"] is False
    # 无引用时不检查（由 citations_must_* 断言负责）
    assert "citation_execution_consistency" not in _names(
        check_turn(None, _res(verdict_policy_id="X-1"), set())
    )


def test_side_effects_come_from_probe_not_agent() -> None:
    e_none = Expect(db_side_effects="none")
    assert check_turn(e_none, _res(), set()).passed
    assert not check_turn(e_none, _res(), {"refund_created"}).passed
    assert check_turn(e_none, _res(), {"human_review_created"}).passed  # 转人工不算 biz 写入
    e_refund = Expect(db_side_effects="refund_created")
    assert check_turn(e_refund, _res(), {"refund_created"}).passed
    assert not check_turn(e_refund, _res(), set()).passed


def test_response_text_and_wording() -> None:
    e = Expect(
        response_must_contain=["89"], response_must_not_contain=["VIP"], no_certainty_wording=True
    )
    assert check_turn(e, _res(reply="订单金额 89 元，可能可以退款"), set()).passed
    bad = check_turn(e, _res(reply="VIP 一定可以退"), set())
    assert _names(bad) == {
        "tool_signature_no_identity": True,
        "response_must_contain": False,
        "response_must_not_contain": False,
        "no_certainty_wording": False,
    }


def test_confidence() -> None:
    assert check_turn(Expect(confidence="low"), _res(confidence="low"), set()).passed
    assert not check_turn(Expect(confidence="low"), _res(), set()).passed
