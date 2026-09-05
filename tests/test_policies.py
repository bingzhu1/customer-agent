"""policies/*.yaml 与 docs/phase0-fixtures.md §5 契约的一致性测试。

期望表在此写死：契约改动时必须同步改这里，而不是反过来。
"""

from decimal import Decimal
from pathlib import Path

import pytest

from cs_agent.domain.enums import PolicyDomain, PolicyEffect, ReasonCode
from cs_agent.policy.schema import PolicySet, load_policies

POLICY_DIR = Path(__file__).resolve().parents[1] / "policies"

# (id, version, domain, effect) —— 与契约 §5 逐行对应
EXPECTED_RULES: dict[str, tuple[int, PolicyDomain, PolicyEffect]] = {
    "REFUND-STD-001": (3, PolicyDomain.REFUND, PolicyEffect.ALLOW_REFUND),
    "MEMBER-GOLD-001": (1, PolicyDomain.MEMBERSHIP, PolicyEffect.ALLOW_REFUND),
    "REFUND-FOOD-001": (2, PolicyDomain.REFUND, PolicyEffect.DENY_REFUND),
    "REFUND-CUSTOM-001": (1, PolicyDomain.REFUND, PolicyEffect.DENY_REFUND),
    "REFUND-UNDELIVERED-001": (1, PolicyDomain.REFUND, PolicyEffect.REQUIRE_HUMAN),
    "SHIP-DELAY-001": (1, PolicyDomain.SHIPPING, PolicyEffect.INFORMATIONAL),
    "SHIP-LOST-001": (1, PolicyDomain.SHIPPING, PolicyEffect.INFORMATIONAL),
    "WARRANTY-STD-001": (2, PolicyDomain.WARRANTY, PolicyEffect.INFORMATIONAL),
    "WARRANTY-EXCL-001": (1, PolicyDomain.WARRANTY, PolicyEffect.INFORMATIONAL),
    "MEMBER-BENEFIT-001": (1, PolicyDomain.MEMBERSHIP, PolicyEffect.INFORMATIONAL),
    "COMPLAINT-SLA-001": (1, PolicyDomain.COMPLAINT, PolicyEffect.INFORMATIONAL),
}

# 契约 §5 末尾：政策未覆盖的主题，YAML 中不得出现
FORBIDDEN_TOPICS = ("价格保护", "发票开具", "账户注销", "海外直邮关税")

EXPECTED_FILES = {"refund", "shipping", "warranty", "membership", "complaint"}


@pytest.fixture(scope="module")
def policies() -> PolicySet:
    return load_policies(POLICY_DIR)


def test_policy_files_present() -> None:
    assert {p.stem for p in POLICY_DIR.glob("*.yaml")} == EXPECTED_FILES


def test_rule_count_and_ids(policies: PolicySet) -> None:
    assert len(policies.rules) == 11
    assert {r.id for r in policies.rules} == set(EXPECTED_RULES)


def test_versions_domains_effects_match_contract(policies: PolicySet) -> None:
    for r in policies.rules:
        version, domain, effect = EXPECTED_RULES[r.id]
        assert r.version == version, r.id
        assert r.domain == domain, r.id
        assert r.effect == effect, r.id


def test_anchors_unique(policies: PolicySet) -> None:
    anchors = [r.anchor for r in policies.rules]
    assert len(anchors) == len(set(anchors))


def test_human_text_and_faq_minimums(policies: PolicySet) -> None:
    for r in policies.rules:
        assert len(r.human_text.strip()) >= 40, r.id
        assert len(r.faq) >= 2, r.id
        for entry in r.faq:
            assert entry.q.strip() and entry.a.strip(), r.id


def test_forbidden_topics_absent(policies: PolicySet) -> None:
    for r in policies.rules:
        texts = [r.title, r.human_text, *(f"{e.q}\n{e.a}" for e in r.faq)]
        for text in texts:
            for topic in FORBIDDEN_TOPICS:
                assert topic not in text, f"{r.id} 含未覆盖主题 {topic!r}"


@pytest.mark.parametrize(
    ("policy_id", "window", "tier"),
    [("REFUND-STD-001", 30, "standard"), ("MEMBER-GOLD-001", 45, "gold")],
)
def test_refund_windows_and_amount_caps(
    policies: PolicySet, policy_id: str, window: int, tier: str
) -> None:
    r = policies.by_id(policy_id)
    assert r.applies_to.item_category == "standard"
    assert r.applies_to.user_tier == tier
    assert r.conditions["days_since_delivery"].lte == window
    assert r.conditions["item_condition"].in_ == ["unused", "unopened"]
    assert r.max_auto_amount == Decimal("200")
    assert r.requires_approval_above == Decimal("200")
    assert r.reason_code_on_pass == ReasonCode.POLICY_SATISFIED
    assert r.reason_code_on_fail == ReasonCode.POLICY_VIOLATION_CONDITION
    assert r.fail_reason_codes == {
        "days_since_delivery": ReasonCode.POLICY_VIOLATION_WINDOW,
        "item_condition": ReasonCode.POLICY_VIOLATION_CONDITION,
    }


@pytest.mark.parametrize(
    ("policy_id", "category"), [("REFUND-FOOD-001", "food"), ("REFUND-CUSTOM-001", "custom")]
)
def test_category_deny_rules(policies: PolicySet, policy_id: str, category: str) -> None:
    r = policies.by_id(policy_id)
    assert r.applies_to.item_category == category
    assert r.conditions == {}
    assert r.reason_code_on_pass == ReasonCode.POLICY_VIOLATION_CATEGORY
    assert r.reason_code_on_fail == ReasonCode.POLICY_AMBIGUOUS


def test_undelivered_rule(policies: PolicySet) -> None:
    r = policies.by_id("REFUND-UNDELIVERED-001")
    assert r.applies_to.item_category == ["standard", "food", "custom"]
    assert r.conditions["order_delivered"].eq is False


def test_decisional_rules_declare_applies_to(policies: PolicySet) -> None:
    for r in policies.rules:
        if r.effect in (PolicyEffect.ALLOW_REFUND, PolicyEffect.DENY_REFUND):
            a = r.applies_to
            assert any((a.item_category, a.user_tier, a.ticket_type)), r.id


def test_informational_rules_carry_no_decision_fields(policies: PolicySet) -> None:
    for r in policies.rules:
        if r.effect == PolicyEffect.INFORMATIONAL:
            assert r.conditions == {}, r.id
            assert r.max_auto_amount is None and r.requires_approval_above is None, r.id
            assert r.reason_code_on_pass is None and r.reason_code_on_fail is None, r.id
