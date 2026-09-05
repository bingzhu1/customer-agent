"""动作状态机（FR-502/504/508）。每个合法迁移与每个非法组合都有用例。"""

from __future__ import annotations

import itertools

import pytest

from cs_agent.actions.state import (
    EXECUTABLE,
    TERMINAL,
    TRANSITIONS,
    WAITING,
    ActionEvent,
    ActionStatus,
    InvalidTransitionError,
    can_transition,
    transition,
)

# 期望表在此写死，与 actions/state.py 的图注释逐条对应。改状态机必须先改这里。
EXPECTED: dict[tuple[ActionStatus, ActionEvent], ActionStatus] = {
    (ActionStatus.PROPOSED, ActionEvent.REQUIRE_CONFIRMATION): ActionStatus.AWAITING_CONFIRMATION,
    (ActionStatus.PROPOSED, ActionEvent.REQUIRE_HUMAN): ActionStatus.AWAITING_HUMAN,
    (ActionStatus.PROPOSED, ActionEvent.REJECT): ActionStatus.REJECTED,
    (ActionStatus.PROPOSED, ActionEvent.EXPIRE): ActionStatus.EXPIRED,
    (ActionStatus.AWAITING_CONFIRMATION, ActionEvent.CONFIRM): ActionStatus.EXECUTING,
    (ActionStatus.AWAITING_CONFIRMATION, ActionEvent.REJECT): ActionStatus.REJECTED,
    (ActionStatus.AWAITING_CONFIRMATION, ActionEvent.EXPIRE): ActionStatus.EXPIRED,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.APPROVE): ActionStatus.EXECUTING,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.EDIT): ActionStatus.AWAITING_HUMAN,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.REJECT): ActionStatus.REJECTED,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.EXPIRE): ActionStatus.EXPIRED,
    (ActionStatus.EXECUTING, ActionEvent.SUCCEED): ActionStatus.SUCCEEDED,
    (ActionStatus.EXECUTING, ActionEvent.FAIL): ActionStatus.FAILED,
    (ActionStatus.FAILED, ActionEvent.RETRY): ActionStatus.EXECUTING,
}


def test_transition_table_matches_the_contract() -> None:
    assert TRANSITIONS == EXPECTED


@pytest.mark.parametrize(
    ("status", "event", "expected"),
    [(s, e, t) for (s, e), t in sorted(EXPECTED.items(), key=str)],
    ids=[f"{s}-{e}" for s, e in sorted(EXPECTED, key=str)],
)
def test_legal_transition(status: ActionStatus, event: ActionEvent, expected: ActionStatus) -> None:
    assert transition(status, event) is expected
    assert can_transition(status, event)


ILLEGAL = [
    (s, e) for s, e in itertools.product(ActionStatus, ActionEvent) if (s, e) not in EXPECTED
]


@pytest.mark.parametrize(("status", "event"), ILLEGAL, ids=[f"{s}-{e}" for s, e in ILLEGAL])
def test_illegal_transition_raises(status: ActionStatus, event: ActionEvent) -> None:
    """非法迁移抛错而不是静默留在原状态——静默会让"已退款的动作又被确认一次"看不出来。"""
    assert not can_transition(status, event)
    with pytest.raises(InvalidTransitionError) as exc:
        transition(status, event)
    assert exc.value.status is status
    assert exc.value.event is event


def test_illegal_set_is_not_trivially_empty() -> None:
    assert len(ILLEGAL) == len(ActionStatus) * len(ActionEvent) - len(EXPECTED)
    assert len(ILLEGAL) > 0


# --- 结构性质 -------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(TERMINAL, key=str))
def test_terminal_states_accept_nothing(status: ActionStatus) -> None:
    """succeeded / rejected / expired 是终态：任何事件都不接受。"""
    assert not any(can_transition(status, e) for e in ActionEvent)


def test_failed_is_not_terminal_because_retry_must_work() -> None:
    """FR-508：执行失败可重试，重试命中同一幂等键，不产生第二次副作用。"""
    assert ActionStatus.FAILED not in TERMINAL
    assert transition(ActionStatus.FAILED, ActionEvent.RETRY) is ActionStatus.EXECUTING


def test_waiting_states_can_expire() -> None:
    """FR-504：只有等待态才谈得上过期。"""
    assert WAITING == {ActionStatus.AWAITING_CONFIRMATION, ActionStatus.AWAITING_HUMAN}
    for status in WAITING:
        assert transition(status, ActionEvent.EXPIRE) is ActionStatus.EXPIRED


def test_executable_states_all_reach_executing() -> None:
    for status in EXECUTABLE:
        events = [e for e in ActionEvent if EXPECTED.get((status, e)) is ActionStatus.EXECUTING]
        assert events, status


def test_only_executing_can_succeed() -> None:
    """成功只可能从 executing 来：不允许从 awaiting_* 直接跳到 succeeded 而跳过执行。"""
    sources = {s for (s, e), t in EXPECTED.items() if t is ActionStatus.SUCCEEDED}
    assert sources == {ActionStatus.EXECUTING}


def test_every_status_is_reachable_from_proposed() -> None:
    """没有孤岛状态。"""
    seen = {ActionStatus.PROPOSED}
    frontier = [ActionStatus.PROPOSED]
    while frontier:
        current = frontier.pop()
        for event in ActionEvent:
            nxt = EXPECTED.get((current, event))
            if nxt is not None and nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(ActionStatus)
