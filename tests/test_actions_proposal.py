"""`ActionProposal` 与幂等键（FR-501/503/605）。纯函数，不连数据库。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cs_agent.actions.proposal import (
    ActionProposal,
    ActionType,
    InvalidProposalError,
    canonical_params,
    idempotency_key,
)

USER = 101
WINDOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
BASE_PARAMS: dict[str, str | int | Decimal] = {
    "order_id": 82913,
    "amount": Decimal("89.00"),
    "reason": "商品未使用",
}


def key(
    *,
    params: dict[str, str | int | Decimal] | None = None,
    user_id: int = USER,
    action_type: ActionType = ActionType.REFUND,
    window_start: datetime = WINDOW,
) -> str:
    return idempotency_key(user_id, action_type, {**BASE_PARAMS, **(params or {})}, window_start)


# --- 构造约束 -------------------------------------------------------------------


def test_refund_requires_order_amount_and_reason() -> None:
    with pytest.raises(InvalidProposalError, match="缺少必填参数"):
        ActionProposal(ActionType.REFUND, {"order_id": 82913})


def test_params_must_not_carry_identity() -> None:
    """红线 1：身份来自 AuthContext，不能藏在提议参数里。"""
    with pytest.raises(InvalidProposalError, match="身份字段"):
        ActionProposal(
            ActionType.REFUND,
            {"order_id": 82913, "amount": Decimal("1"), "reason": "x", "user_id": 202},
        )


def test_proposal_is_frozen() -> None:
    p = ActionProposal(ActionType.REFUND, dict(BASE_PARAMS))
    with pytest.raises(AttributeError):
        p.action_type = ActionType.CREATE_TICKET  # type: ignore[misc]


def test_as_jsonb_turns_decimal_into_a_fixed_point_string() -> None:
    """JSONB 里不放 Decimal，也不放 float——float 会把 0.1+0.2 的问题带进金额。"""
    assert ActionProposal(ActionType.REFUND, dict(BASE_PARAMS)).as_jsonb() == {
        "order_id": 82913,
        "amount": "89",
        "reason": "商品未使用",
    }


# --- 同参数同窗口必同键 -----------------------------------------------------------


def test_same_inputs_give_the_same_key() -> None:
    assert key() == key()


def test_key_ignores_dict_ordering() -> None:
    """网络重试重建的 params 顺序可能不同，键必须一样。"""
    reordered = {"reason": "商品未使用", "amount": Decimal("89.00"), "order_id": 82913}
    assert idempotency_key(USER, ActionType.REFUND, reordered, WINDOW) == key()


@pytest.mark.parametrize("amount", ["89", "89.0", "89.00", "89.000"])
def test_key_ignores_decimal_trailing_zeros(amount: str) -> None:
    """89 与 89.00 是同一笔钱，必须是同一个键，否则重试会退两次。"""
    assert key(params={"amount": Decimal(amount)}) == key()


def test_key_ignores_timezone_spelling() -> None:
    """同一时刻用不同时区写出来，仍是同一个窗口。"""
    shanghai = datetime(2026, 9, 5, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    assert key(window_start=shanghai) == key()


# --- 任一变化必不同键（FR-605）-----------------------------------------------------


@pytest.mark.parametrize(
    ("label", "changed"),
    [
        ("金额", lambda: key(params={"amount": Decimal("88.00")})),
        ("订单号", lambda: key(params={"order_id": 82914})),
        ("理由", lambda: key(params={"reason": "改主意了"})),
        ("用户", lambda: key(user_id=202)),
        ("动作类型", lambda: key(action_type=ActionType.CREATE_TICKET)),
        ("窗口", lambda: key(window_start=WINDOW + timedelta(seconds=1))),
    ],
)
def test_any_change_gives_a_different_key(label: str, changed: Callable[[], str]) -> None:
    """人工审批改了金额就是另一笔动作（FR-605），不能命中旧键把原结果当成"已执行"。"""
    assert changed() != key(), label


def test_added_param_changes_the_key() -> None:
    assert key(params={"note": "加急"}) != key()


# --- 形状 -----------------------------------------------------------------------


def test_key_is_a_sha256_hex_digest() -> None:
    k = key()
    assert len(k) == 64
    assert set(k) <= set("0123456789abcdef")


def test_canonical_params_is_sorted_compact_and_keeps_chinese() -> None:
    assert canonical_params(BASE_PARAMS) == '{"amount":"89","order_id":82913,"reason":"商品未使用"}'


def test_proposal_shortcut_matches_the_function() -> None:
    p = ActionProposal(ActionType.REFUND, dict(BASE_PARAMS))
    assert p.idempotency_key(USER, WINDOW) == key()
