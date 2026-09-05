"""策略引擎测试（FR-401/402/408）。

期望值来自 `docs/phase0-fixtures.md`：§2 关键订单表、§5 策略表、§8 effect→决策映射。
**契约改动时改这里的期望表，不是反过来改代码去迁就测试。**

规则一律从真实的 `policies/` 目录加载，不在测试里手写规则副本——
手写副本会让"YAML 是唯一事实来源"（ADR-0006）失效。
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from cs_agent.domain.enums import (
    ItemCategory,
    ItemCondition,
    PolicyEffect,
    ReasonCode,
    UserTier,
)
from cs_agent.policy.engine import PolicyOutcome, PolicyVerdict, evaluate
from cs_agent.policy.facts import CONDITION_FIELDS, PolicyFacts
from cs_agent.policy.schema import AppliesTo, PolicySet, load_policies

POLICY_DIR = Path(__file__).resolve().parents[1] / "policies"


@pytest.fixture(scope="module")
def policies() -> PolicySet:
    return load_policies(POLICY_DIR)


def facts(
    order_id: int,
    *,
    tier: UserTier = UserTier.STANDARD,
    category: ItemCategory = ItemCategory.STANDARD,
    condition: ItemCondition = ItemCondition.UNUSED,
    amount: str = "100.00",
    delivered: bool = True,
    days: int | None = 5,
    prior_refund: bool = False,
) -> PolicyFacts:
    return PolicyFacts(
        order_id=order_id,
        user_tier=tier,
        item_category=category,
        item_condition=condition,
        order_amount=Decimal(amount),
        order_delivered=delivered,
        days_since_delivery=days,
        prior_refund_exists=prior_refund,
    )


# --- 契约 §2 关键订单：16 行逐条 -------------------------------------------------

# (order_id, facts, 期望 outcome, policy_id, policy_version, reason_code)
CONTRACT_ORDERS: list[tuple[int, PolicyFacts, PolicyOutcome, str, int, ReasonCode]] = [
    (
        82913,
        facts(82913, amount="89.00", days=12),
        PolicyOutcome.ALLOW,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        82914,
        facts(82914, condition=ItemCondition.UNOPENED, amount="150.00", days=30),
        PolicyOutcome.ALLOW,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        82915,
        facts(82915, amount="120.00", days=31),
        PolicyOutcome.DENY,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_VIOLATION_WINDOW,
    ),
    (
        82916,
        facts(
            82916,
            category=ItemCategory.FOOD,
            condition=ItemCondition.UNOPENED,
            amount="68.00",
            days=3,
        ),
        PolicyOutcome.DENY,
        "REFUND-FOOD-001",
        2,
        ReasonCode.POLICY_VIOLATION_CATEGORY,
    ),
    (
        82917,
        facts(82917, category=ItemCategory.CUSTOM, amount="260.00", days=5),
        PolicyOutcome.DENY,
        "REFUND-CUSTOM-001",
        1,
        ReasonCode.POLICY_VIOLATION_CATEGORY,
    ),
    (
        82918,
        facts(82918, amount="620.00", days=5),
        PolicyOutcome.ALLOW,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        82919,
        facts(82919, amount="199.00", delivered=False, days=None),
        PolicyOutcome.REQUIRE_HUMAN,
        "REFUND-UNDELIVERED-001",
        1,
        ReasonCode.POLICY_AMBIGUOUS,
    ),
    (
        82920,
        facts(82920, condition=ItemCondition.USED, amount="99.00", days=8),
        PolicyOutcome.DENY,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_VIOLATION_CONDITION,
    ),
    (
        82921,
        facts(82921, amount="75.00", days=6),
        PolicyOutcome.ALLOW,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        82922,
        facts(82922, amount="45.00", days=20, prior_refund=True),
        PolicyOutcome.ALLOW,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        82923,
        facts(82923, amount="138.00", delivered=False, days=None),
        PolicyOutcome.REQUIRE_HUMAN,
        "REFUND-UNDELIVERED-001",
        1,
        ReasonCode.POLICY_AMBIGUOUS,
    ),
    (
        82930,
        facts(82930, tier=UserTier.GOLD, amount="180.00", days=40),
        PolicyOutcome.ALLOW,
        "MEMBER-GOLD-001",
        1,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        82931,
        facts(82931, tier=UserTier.GOLD, amount="88.00", days=50),
        PolicyOutcome.DENY,
        "MEMBER-GOLD-001",
        1,
        ReasonCode.POLICY_VIOLATION_WINDOW,
    ),
    (
        82932,
        facts(82932, tier=UserTier.GOLD, amount="350.00", days=10),
        PolicyOutcome.ALLOW,
        "MEMBER-GOLD-001",
        1,
        ReasonCode.POLICY_SATISFIED,
    ),
    # 90210 / 90211 属于他人：策略层照常给出资格结论，归属由决策层规则 1 拦截。
    (
        90210,
        facts(90210, amount="199.00", days=2),
        PolicyOutcome.ALLOW,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        90211,
        facts(90211, amount="59.00", days=4),
        PolicyOutcome.ALLOW,
        "REFUND-STD-001",
        3,
        ReasonCode.POLICY_SATISFIED,
    ),
]


def test_contract_covers_all_key_orders() -> None:
    """契约 §2 除"不存在的 77777"外共 16 个关键订单，一个都不能漏。"""
    assert len(CONTRACT_ORDERS) == 16
    assert len({row[0] for row in CONTRACT_ORDERS}) == 16


@pytest.mark.parametrize(
    ("order_id", "f", "outcome", "policy_id", "version", "reason"),
    CONTRACT_ORDERS,
    ids=[str(row[0]) for row in CONTRACT_ORDERS],
)
def test_contract_key_orders(
    policies: PolicySet,
    order_id: int,
    f: PolicyFacts,
    outcome: PolicyOutcome,
    policy_id: str,
    version: int,
    reason: ReasonCode,
) -> None:
    v = evaluate(f, policies)
    assert (v.outcome, v.policy_id, v.policy_version, v.reason_code) == (
        outcome,
        policy_id,
        version,
        reason,
    )


# --- 边界值（FR-408）------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "outcome", "reason"),
    [
        (30, PolicyOutcome.ALLOW, ReasonCode.POLICY_SATISFIED),
        (31, PolicyOutcome.DENY, ReasonCode.POLICY_VIOLATION_WINDOW),
    ],
)
def test_standard_window_boundary(
    policies: PolicySet, days: int, outcome: PolicyOutcome, reason: ReasonCode
) -> None:
    """普通会员窗口 30 天，含第 30 天。"""
    v = evaluate(facts(1, days=days), policies)
    assert (v.outcome, v.policy_id, v.reason_code) == (outcome, "REFUND-STD-001", reason)


@pytest.mark.parametrize(
    ("days", "outcome", "reason"),
    [
        (45, PolicyOutcome.ALLOW, ReasonCode.POLICY_SATISFIED),
        (46, PolicyOutcome.DENY, ReasonCode.POLICY_VIOLATION_WINDOW),
    ],
)
def test_gold_window_boundary(
    policies: PolicySet, days: int, outcome: PolicyOutcome, reason: ReasonCode
) -> None:
    """金卡窗口 45 天，含第 45 天。"""
    v = evaluate(facts(1, tier=UserTier.GOLD, days=days), policies)
    assert (v.outcome, v.policy_id, v.reason_code) == (outcome, "MEMBER-GOLD-001", reason)


@pytest.mark.parametrize("amount", ["200.00", "200.01"])
def test_amount_never_changes_verdict(policies: PolicySet, amount: str) -> None:
    """金额不参与资格判定：200 与 200.01 都是 ALLOW，限额只作为 max_auto_amount 回带，
    是否需要人工审批由决策层规则 10 决定（见 tests/test_decision_matrix.py）。"""
    v = evaluate(facts(1, amount=amount, days=5), policies)
    assert v.outcome is PolicyOutcome.ALLOW
    assert v.max_auto_amount == Decimal("200")


@pytest.mark.parametrize("condition", [ItemCondition.USED, ItemCondition.DAMAGED])
def test_condition_used_or_damaged_denied(policies: PolicySet, condition: ItemCondition) -> None:
    v = evaluate(facts(1, condition=condition, days=5), policies)
    assert (v.outcome, v.policy_id, v.reason_code, v.failed_condition) == (
        PolicyOutcome.DENY,
        "REFUND-STD-001",
        ReasonCode.POLICY_VIOLATION_CONDITION,
        "item_condition",
    )


@pytest.mark.parametrize(
    "condition", [ItemCondition.UNUSED, ItemCondition.UNOPENED, ItemCondition.USED]
)
def test_undelivered_always_require_human(policies: PolicySet, condition: ItemCondition) -> None:
    """未签收订单一律走人工拦截，不因商品状态或窗口条件退化成 DENY（契约 §2 订单 82919）。"""
    v = evaluate(facts(1, condition=condition, delivered=False, days=None), policies)
    assert (v.outcome, v.policy_id, v.reason_code) == (
        PolicyOutcome.REQUIRE_HUMAN,
        "REFUND-UNDELIVERED-001",
        ReasonCode.POLICY_AMBIGUOUS,
    )


# --- 排障字段 -------------------------------------------------------------------


def test_matched_and_failed_conditions_reported(policies: PolicySet) -> None:
    ok = evaluate(facts(1, days=10), policies)
    assert ok.matched_conditions == ("days_since_delivery", "item_condition")
    assert ok.failed_condition is None

    bad = evaluate(facts(1, days=99), policies)
    assert bad.matched_conditions == ()
    assert bad.failed_condition == "days_since_delivery"


# --- informational 永不参与判定 ---------------------------------------------------

ALL_FACT_COMBOS = [
    facts(
        1,
        tier=tier,
        category=category,
        condition=condition,
        amount=amount,
        delivered=delivered,
        days=days,
        prior_refund=prior,
    )
    for tier, category, condition, amount, delivered, days, prior in itertools.product(
        UserTier,
        ItemCategory,
        ItemCondition,
        ["0.00", "199.99", "200.00", "200.01", "5000.00"],
        [True, False],
        [None, 0, 30, 31, 45, 46, 365],
        [True, False],
    )
]


def test_informational_rules_never_produce_a_verdict(policies: PolicySet) -> None:
    """契约 §8 第 4 行：informational 只参与 RAG 回答，任何事实组合下都不得成为判定依据。"""
    informational = {r.id for r in policies.rules if r.effect is PolicyEffect.INFORMATIONAL}
    assert informational  # 真实策略集里确实有 informational 规则
    produced = {
        v.policy_id
        for v in (evaluate(f, policies) for f in ALL_FACT_COMBOS)
        if v.policy_id is not None
    }
    assert produced.isdisjoint(informational)


def test_informational_only_policy_set_yields_no_rule(policies: PolicySet) -> None:
    only_info = PolicySet(
        rules=[r for r in policies.rules if r.effect is PolicyEffect.INFORMATIONAL]
    )
    for f in ALL_FACT_COMBOS:
        v = evaluate(f, only_info)
        assert v.outcome is PolicyOutcome.NO_RULE
        assert v.reason_code is ReasonCode.POLICY_AMBIGUOUS
        assert v.policy_id is None


# --- 冲突与无规则 ---------------------------------------------------------------


def test_two_matching_allow_rules_are_ambiguous(policies: PolicySet) -> None:
    """冲突规则集由真实规则派生（改 applies_to 让金卡规则也命中普通会员），不手写副本。"""
    clone = policies.by_id("MEMBER-GOLD-001").model_copy(
        update={
            "id": "MEMBER-GOLD-002",
            "anchor": "membership#gold-refund-window-clone",
            "applies_to": AppliesTo(item_category="standard", user_tier="standard"),
        }
    )
    conflicting = PolicySet(rules=[*policies.rules, clone])
    v = evaluate(facts(1, days=10), conflicting)
    assert v.outcome is PolicyOutcome.AMBIGUOUS
    assert v.reason_code is ReasonCode.POLICY_AMBIGUOUS
    assert v.policy_id is None
    assert set(v.candidates) == {"REFUND-STD-001", "MEMBER-GOLD-002"}


def test_verdict_always_carries_id_and_version_when_a_rule_matched(
    policies: PolicySet,
) -> None:
    """FR-402：只要有规则命中，policy_id 与 policy_version 必须成对回带。"""
    for f in ALL_FACT_COMBOS:
        v = evaluate(f, policies)
        assert isinstance(v, PolicyVerdict)
        assert (v.policy_id is None) == (v.policy_version is None)
        assert v.outcome is not PolicyOutcome.NO_RULE or v.policy_id is None


# --- 纯函数与红线 3 --------------------------------------------------------------


def test_evaluate_is_pure(policies: PolicySet) -> None:
    f = facts(1, days=10)
    assert evaluate(f, policies) == evaluate(f, policies)


def test_facts_has_no_memory_field() -> None:
    """ADR-0009 强制手段第 3 条：结构上不存在记忆类输入，越权不可表达。"""
    fields = set(PolicyFacts.__dataclass_fields__)
    assert fields == CONDITION_FIELDS | {"order_id"}
    assert not any("memory" in name or "vip" in name for name in fields)
