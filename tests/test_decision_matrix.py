"""决策层升级矩阵测试（PRD §9.4，FR-404/405/406）。

三件事：
1. §9.4 的 19 个分支（16 条规则 + 6b + 10.5 + 14b）每个至少一个命中用例；
2. 顺序关系不能被改坏——规则 1 压过一切、10.5 压过 12、11 在 12 之前、14 缺引用退回 14b；
3. 契约 §2 的关键订单从 PolicyFacts 一路走到终态，与契约"关键事实 → 期望"一致。
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from cs_agent.decision import Decision, DecisionInput, decide
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.policy.engine import PolicyOutcome, PolicyVerdict, evaluate
from cs_agent.policy.facts import PolicyFacts
from cs_agent.policy.schema import PolicySet, load_policies

# 同目录测试模块：契约 §2 的 16 行事实表只写一份，避免两处漂移。
from test_policy_engine import CONTRACT_ORDERS, facts

POLICY_DIR = Path(__file__).resolve().parents[1] / "policies"

TAU_LOW = 0.30
TAU_HIGH = 0.80


@pytest.fixture(scope="module")
def policies() -> PolicySet:
    return load_policies(POLICY_DIR)


def allow_verdict(max_auto: str | None = "200") -> PolicyVerdict:
    return PolicyVerdict(
        outcome=PolicyOutcome.ALLOW,
        reason_code=ReasonCode.POLICY_SATISFIED,
        policy_id="REFUND-STD-001",
        policy_version=3,
        max_auto_amount=None if max_auto is None else Decimal(max_auto),
    )


def simple_verdict(outcome: PolicyOutcome, reason: ReasonCode) -> PolicyVerdict:
    return PolicyVerdict(outcome=outcome, reason_code=reason, policy_id="X-Y-001", policy_version=1)


# --- 每条规则至少一个命中用例 ------------------------------------------------------

# (规则号, DecisionInput, 期望 outcome, 期望 reason_code)
RULE_HITS: list[tuple[str, DecisionInput, DecisionOutcome, ReasonCode]] = [
    (
        "1",
        DecisionInput(ownership_ok=False),
        DecisionOutcome.DENY,
        ReasonCode.OWNERSHIP_MISMATCH,
    ),
    (
        "2",
        DecisionInput(injection_suspected=True),
        DecisionOutcome.DENY,
        ReasonCode.SUSPECTED_INJECTION,
    ),
    (
        "3",
        DecisionInput(role_sufficient=False),
        DecisionOutcome.DENY,
        ReasonCode.AUTH_INSUFFICIENT,
    ),
    (
        "4",
        DecisionInput(customer_requests_human=True),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.CUSTOMER_ESCALATION_REQUEST,
    ),
    (
        "5",
        DecisionInput(high_negative_sentiment=True),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.HIGH_NEGATIVE_SENTIMENT,
    ),
    (
        "6",
        DecisionInput(repeated_tool_failure=True),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.TOOL_FAILURE_REPEATED,
    ),
    (
        "6b",
        DecisionInput(tool_budget_exceeded=True),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.TOOL_BUDGET_EXCEEDED,
    ),
    (
        "7",
        DecisionInput(dependency_unavailable=True),
        DecisionOutcome.DEGRADE,
        ReasonCode.DEPENDENCY_UNAVAILABLE,
    ),
    (
        "8",
        DecisionInput(
            verdict=simple_verdict(PolicyOutcome.DENY, ReasonCode.POLICY_VIOLATION_WINDOW),
            is_write_intent=True,
        ),
        DecisionOutcome.DENY,
        ReasonCode.POLICY_VIOLATION_WINDOW,
    ),
    (
        "9",
        DecisionInput(
            verdict=simple_verdict(PolicyOutcome.NO_RULE, ReasonCode.POLICY_AMBIGUOUS),
            is_eligibility_intent=True,
        ),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.POLICY_AMBIGUOUS,
    ),
    (
        "10",
        DecisionInput(verdict=allow_verdict(), amount=Decimal("620.00"), is_write_intent=True),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT,
    ),
    (
        "10.5",
        DecisionInput(
            verdict=allow_verdict(),
            amount=Decimal("89.00"),
            is_write_intent=True,
            retrieval_max_score=0.5,
            tau_low=TAU_LOW,
            tau_high=TAU_HIGH,
            has_citable_chunk=True,
        ),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.LOW_CONFIDENCE_ON_DECISION,
    ),
    (
        "11",
        DecisionInput(
            verdict=allow_verdict(),
            amount=Decimal("45.00"),
            is_write_intent=True,
            idempotent_replay=True,
        ),
        DecisionOutcome.ANSWER,
        ReasonCode.IDEMPOTENT_REPLAY,
    ),
    (
        "12",
        DecisionInput(verdict=allow_verdict(), amount=Decimal("89.00"), is_write_intent=True),
        DecisionOutcome.REQUIRE_CONFIRMATION,
        ReasonCode.POLICY_SATISFIED,
    ),
    (
        "13",
        DecisionInput(retrieval_max_score=0.10, tau_low=TAU_LOW, tau_high=TAU_HIGH),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.RETRIEVAL_NO_RESULT,
    ),
    (
        "14",
        DecisionInput(
            retrieval_max_score=0.50,
            tau_low=TAU_LOW,
            tau_high=TAU_HIGH,
            has_citable_chunk=True,
        ),
        DecisionOutcome.ANSWER,
        ReasonCode.RETRIEVAL_LOW_CONFIDENCE,
    ),
    (
        "14b",
        DecisionInput(
            retrieval_max_score=0.50,
            tau_low=TAU_LOW,
            tau_high=TAU_HIGH,
            has_citable_chunk=False,
        ),
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.RETRIEVAL_NO_RESULT,
    ),
    (
        "15",
        DecisionInput(missing_entity=True),
        DecisionOutcome.REQUEST_INFO,
        ReasonCode.MISSING_ENTITY,
    ),
    (
        "16",
        DecisionInput(retrieval_max_score=0.95, tau_low=TAU_LOW, tau_high=TAU_HIGH),
        DecisionOutcome.ANSWER,
        ReasonCode.OK,
    ),
]


def test_every_matrix_branch_has_a_case() -> None:
    """§9.4 的 19 个分支一个不落。"""
    expected = {str(i) for i in range(1, 17)} | {"6b", "10.5", "14b"}
    assert {row[0] for row in RULE_HITS} == expected


@pytest.mark.parametrize(
    ("rule_no", "inp", "outcome", "reason"), RULE_HITS, ids=[row[0] for row in RULE_HITS]
)
def test_rule_hit(
    rule_no: str, inp: DecisionInput, outcome: DecisionOutcome, reason: ReasonCode
) -> None:
    assert decide(inp) == Decision(outcome, reason, rule_no)


# --- 优先级 ---------------------------------------------------------------------


def test_rule_1_beats_everything() -> None:
    """归属不符时，其余所有信号都不能改变结果，也不能泄露订单是否存在。"""
    assert decide(
        DecisionInput(
            ownership_ok=False,
            injection_suspected=True,
            role_sufficient=False,
            customer_requests_human=True,
            high_negative_sentiment=True,
            repeated_tool_failure=True,
            dependency_unavailable=True,
            verdict=allow_verdict(),
            amount=Decimal("10000"),
            is_write_intent=True,
            is_eligibility_intent=True,
            retrieval_max_score=0.05,
            tau_low=TAU_LOW,
            tau_high=TAU_HIGH,
            has_citable_chunk=True,
            idempotent_replay=True,
            missing_entity=True,
        )
    ) == Decision(DecisionOutcome.DENY, ReasonCode.OWNERSHIP_MISMATCH, "1")


@pytest.mark.parametrize(
    ("earlier", "later", "inp"),
    [
        # 2 压过 3~16：注入嫌疑下不得走到任何写路径
        (
            "2",
            "12",
            DecisionInput(
                injection_suspected=True,
                verdict=allow_verdict(),
                amount=Decimal("89.00"),
                is_write_intent=True,
            ),
        ),
        # 6b 压过 12：工具预算耗尽时不许还走到"请用户确认"
        (
            "6b",
            "12",
            DecisionInput(
                tool_budget_exceeded=True,
                verdict=allow_verdict(),
                amount=Decimal("89.00"),
                is_write_intent=True,
            ),
        ),
        # 6 压过 6b：同时成立时报更具体的连续失败
        (
            "6",
            "6b",
            DecisionInput(repeated_tool_failure=True, tool_budget_exceeded=True),
        ),
        # 8 压过 9：有明确拒绝就不该报"政策歧义"
        (
            "8",
            "9",
            DecisionInput(
                verdict=simple_verdict(PolicyOutcome.DENY, ReasonCode.POLICY_VIOLATION_CATEGORY),
                is_eligibility_intent=True,
            ),
        ),
        # 10 压过 11：超限额优先于幂等重放
        (
            "10",
            "11",
            DecisionInput(
                verdict=allow_verdict(),
                amount=Decimal("620.00"),
                is_write_intent=True,
                idempotent_replay=True,
            ),
        ),
        # 10.5 压过 12：写操作 + 低置信一律转人工，不许走到"请确认"
        (
            "10.5",
            "12",
            DecisionInput(
                verdict=allow_verdict(),
                amount=Decimal("89.00"),
                is_write_intent=True,
                retrieval_max_score=0.50,
                tau_low=TAU_LOW,
                tau_high=TAU_HIGH,
                has_citable_chunk=True,
            ),
        ),
        # 11 压过 12：已经退过就返回原结果，不再要用户确认第二次
        (
            "11",
            "12",
            DecisionInput(
                verdict=allow_verdict(),
                amount=Decimal("45.00"),
                is_write_intent=True,
                idempotent_replay=True,
            ),
        ),
        # 13 压过 15：检索完全无结果时先转人工
        (
            "13",
            "15",
            DecisionInput(
                retrieval_max_score=0.10,
                tau_low=TAU_LOW,
                tau_high=TAU_HIGH,
                missing_entity=True,
            ),
        ),
    ],
    ids=["2>12", "6b>12", "6>6b", "8>9", "10>11", "10.5>12", "11>12", "13>15"],
)
def test_priority(earlier: str, later: str, inp: DecisionInput) -> None:
    """两条规则同时满足时，必须由靠前的那条给出结论。"""
    assert earlier != later
    assert decide(inp).rule_no == earlier


def test_rule_14_requires_a_citable_chunk() -> None:
    """低置信不等于允许无据回答：引用不出来就退回 14b 转人工（§9.4 约束 3）。"""
    with_citation = decide(
        DecisionInput(
            retrieval_max_score=0.50, tau_low=TAU_LOW, tau_high=TAU_HIGH, has_citable_chunk=True
        )
    )
    without = decide(
        DecisionInput(
            retrieval_max_score=0.50, tau_low=TAU_LOW, tau_high=TAU_HIGH, has_citable_chunk=False
        )
    )
    assert with_citation == Decision(
        DecisionOutcome.ANSWER, ReasonCode.RETRIEVAL_LOW_CONFIDENCE, "14"
    )
    assert without == Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.RETRIEVAL_NO_RESULT, "14b")


def test_low_confidence_on_eligibility_never_answers() -> None:
    """ "看似信息类实则资格判定"的陷阱：只要 is_eligibility_intent，低置信一律 10.5。"""
    for score in (0.05, 0.50, 0.79):
        d = decide(
            DecisionInput(
                is_eligibility_intent=True,
                verdict=allow_verdict(),
                amount=Decimal("10.00"),
                retrieval_max_score=score,
                tau_low=TAU_LOW,
                tau_high=TAU_HIGH,
                has_citable_chunk=True,
            )
        )
        assert d == Decision(
            DecisionOutcome.REQUIRE_HUMAN, ReasonCode.LOW_CONFIDENCE_ON_DECISION, "10.5"
        )


def test_missing_verdict_on_write_intent_is_not_allowed_through() -> None:
    """要判资格却没有 PolicyVerdict：按政策歧义转人工，绝不默认放行。"""
    assert decide(DecisionInput(is_write_intent=True)) == Decision(
        DecisionOutcome.REQUIRE_HUMAN, ReasonCode.POLICY_AMBIGUOUS, "9"
    )


def test_amount_boundary_200_vs_200_01() -> None:
    """max_auto_amount = 200，含 200；200.01 起需人工审批（契约 §5 / §8）。"""
    inside = DecisionInput(verdict=allow_verdict(), amount=Decimal("200.00"), is_write_intent=True)
    outside = DecisionInput(verdict=allow_verdict(), amount=Decimal("200.01"), is_write_intent=True)
    assert decide(inside) == Decision(
        DecisionOutcome.REQUIRE_CONFIRMATION, ReasonCode.POLICY_SATISFIED, "12"
    )
    assert decide(outside) == Decision(
        DecisionOutcome.REQUIRE_HUMAN, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT, "10"
    )


# --- 契约 §2 端到端：PolicyFacts → verdict → 终态 ---------------------------------

# order_id -> (ownership_ok, is_write_intent, idempotent_replay, outcome, reason, rule_no)
CONTRACT_DECISIONS: dict[int, tuple[bool, bool, bool, DecisionOutcome, ReasonCode, str]] = {
    82913: (
        True,
        True,
        False,
        DecisionOutcome.REQUIRE_CONFIRMATION,
        ReasonCode.POLICY_SATISFIED,
        "12",
    ),
    82914: (
        True,
        True,
        False,
        DecisionOutcome.REQUIRE_CONFIRMATION,
        ReasonCode.POLICY_SATISFIED,
        "12",
    ),
    82915: (True, True, False, DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_WINDOW, "8"),
    82916: (True, True, False, DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_CATEGORY, "8"),
    82917: (True, True, False, DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_CATEGORY, "8"),
    82918: (
        True,
        True,
        False,
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT,
        "10",
    ),
    82919: (True, True, False, DecisionOutcome.REQUIRE_HUMAN, ReasonCode.POLICY_AMBIGUOUS, "9"),
    82920: (True, True, False, DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_CONDITION, "8"),
    82921: (
        True,
        True,
        False,
        DecisionOutcome.REQUIRE_CONFIRMATION,
        ReasonCode.POLICY_SATISFIED,
        "12",
    ),
    82922: (True, True, True, DecisionOutcome.ANSWER, ReasonCode.IDEMPOTENT_REPLAY, "11"),
    82923: (True, True, False, DecisionOutcome.REQUIRE_HUMAN, ReasonCode.POLICY_AMBIGUOUS, "9"),
    82930: (
        True,
        True,
        False,
        DecisionOutcome.REQUIRE_CONFIRMATION,
        ReasonCode.POLICY_SATISFIED,
        "12",
    ),
    82931: (True, True, False, DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_WINDOW, "8"),
    82932: (
        True,
        True,
        False,
        DecisionOutcome.REQUIRE_HUMAN,
        ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT,
        "10",
    ),
    90210: (False, True, False, DecisionOutcome.DENY, ReasonCode.OWNERSHIP_MISMATCH, "1"),
    90211: (False, False, False, DecisionOutcome.DENY, ReasonCode.OWNERSHIP_MISMATCH, "1"),
}


@pytest.mark.parametrize(
    ("order_id", "f"),
    [(row[0], row[1]) for row in CONTRACT_ORDERS],
    ids=[str(row[0]) for row in CONTRACT_ORDERS],
)
def test_contract_orders_end_to_end(policies: PolicySet, order_id: int, f: PolicyFacts) -> None:
    ownership, write, replay, outcome, reason, rule_no = CONTRACT_DECISIONS[order_id]
    d = decide(
        DecisionInput(
            ownership_ok=ownership,
            verdict=evaluate(f, policies),
            amount=f.order_amount,
            is_write_intent=write,
            idempotent_replay=replay,
        )
    )
    assert d == Decision(outcome, reason, rule_no)


def test_injected_order_note_does_not_change_decision(policies: PolicySet) -> None:
    """契约 §4：82921 的 note 含注入文本，决策必须零变化；
    即便触发注入检测也只能拒绝，不得放行任何写操作。"""
    f = facts(82921, amount="75.00", days=6)
    base = DecisionInput(verdict=evaluate(f, policies), amount=f.order_amount, is_write_intent=True)
    assert decide(base) == Decision(
        DecisionOutcome.REQUIRE_CONFIRMATION, ReasonCode.POLICY_SATISFIED, "12"
    )
    flagged = decide(
        DecisionInput(
            verdict=base.verdict,
            amount=base.amount,
            is_write_intent=True,
            injection_suspected=True,
        )
    )
    assert flagged == Decision(DecisionOutcome.DENY, ReasonCode.SUSPECTED_INJECTION, "2")


# --- 穷举：任意输入组合都必须落在枚举内 ---------------------------------------------

VERDICT_VARIANTS: list[PolicyVerdict | None] = [
    None,
    allow_verdict(),
    allow_verdict(max_auto=None),
    simple_verdict(PolicyOutcome.DENY, ReasonCode.POLICY_VIOLATION_CONDITION),
    simple_verdict(PolicyOutcome.REQUIRE_HUMAN, ReasonCode.POLICY_AMBIGUOUS),
    simple_verdict(PolicyOutcome.NO_RULE, ReasonCode.POLICY_AMBIGUOUS),
    simple_verdict(PolicyOutcome.AMBIGUOUS, ReasonCode.POLICY_AMBIGUOUS),
]
SCORE_VARIANTS: list[float | None] = [None, 0.05, 0.50, 0.95]
AMOUNT_VARIANTS: list[Decimal | None] = [None, Decimal("100.00"), Decimal("250.00")]
VALID_RULE_NOS = {str(i) for i in range(1, 17)} | {"6b", "10.5", "14b"}
# 预先算好：放在循环里会让 68 万次迭代每次重建一个集合
VALID_OUTCOMES = frozenset(DecisionOutcome)
VALID_REASONS = frozenset(ReasonCode)
TRIPLES = [
    (v, s, a) for v, s, a in itertools.product(VERDICT_VARIANTS, SCORE_VARIANTS, AMOUNT_VARIANTS)
]


def test_decide_is_total_over_all_input_combinations() -> None:
    """穷举 2^13 × verdict × score × amount 组合：终态与 reason_code 必须在枚举内，
    rule_no 必须非空且是 §9.4 中真实存在的行号——决策层没有"掉出表外"的输入。"""
    checked = 0
    for flags in itertools.product([False, True], repeat=13):
        (
            ownership_ok,
            injection,
            role_ok,
            wants_human,
            sentiment,
            tool_fail,
            budget_out,
            dep_down,
            write,
            eligibility,
            citable,
            replay,
            missing,
        ) = flags
        for verdict, score, amount in TRIPLES:
            d = decide(
                DecisionInput(
                    ownership_ok=ownership_ok,
                    injection_suspected=injection,
                    role_sufficient=role_ok,
                    customer_requests_human=wants_human,
                    high_negative_sentiment=sentiment,
                    repeated_tool_failure=tool_fail,
                    tool_budget_exceeded=budget_out,
                    dependency_unavailable=dep_down,
                    verdict=verdict,
                    amount=amount,
                    is_write_intent=write,
                    is_eligibility_intent=eligibility,
                    retrieval_max_score=score,
                    tau_low=TAU_LOW,
                    tau_high=TAU_HIGH,
                    has_citable_chunk=citable,
                    idempotent_replay=replay,
                    missing_entity=missing,
                )
            )
            assert d.outcome in VALID_OUTCOMES
            assert d.reason_code in VALID_REASONS
            assert d.rule_no in VALID_RULE_NOS
            checked += 1
    assert checked == 2**13 * len(TRIPLES)


def test_write_intent_never_reaches_confirmation_without_an_allow_verdict() -> None:
    """安全不变式：只要没有 ALLOW 判定，任何输入组合都不可能得到 REQUIRE_CONFIRMATION。"""
    for flags in itertools.product([False, True], repeat=6):
        write, eligibility, citable, replay, missing, ownership_ok = flags
        for verdict in VERDICT_VARIANTS:
            if verdict is not None and verdict.outcome is PolicyOutcome.ALLOW:
                continue
            for score in SCORE_VARIANTS:
                d = decide(
                    DecisionInput(
                        ownership_ok=ownership_ok,
                        verdict=verdict,
                        amount=Decimal("100.00"),
                        is_write_intent=write,
                        is_eligibility_intent=eligibility,
                        retrieval_max_score=score,
                        tau_low=TAU_LOW,
                        tau_high=TAU_HIGH,
                        has_citable_chunk=citable,
                        idempotent_replay=replay,
                        missing_entity=missing,
                    )
                )
                assert d.outcome is not DecisionOutcome.REQUIRE_CONFIRMATION
