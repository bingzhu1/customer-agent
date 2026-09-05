"""受约束话术骨架的测试（FR-407、FR-308、§9.4"用户看到"列）。

三个必须守住的性质：
1. 矩阵能产出的每个 (终态, reason_code) 都有模板，`render` 不会抛异常；
2. `OWNERSHIP_MISMATCH` 的文案对"他人订单"与"不存在订单"逐字相同；
3. 低置信声明带 handoff_offer，且不含 `cs_agent.domain.wording` 词表里的确定性措辞。
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest

from cs_agent.decision import Decision, DecisionInput, decide
from cs_agent.decision.templates import (
    HANDOFF_OFFER,
    NOT_FOUND_TEXT,
    TEMPLATES,
    MissingTemplateError,
    TemplateVars,
    degrade,
    deny,
    low_confidence_disclosure,
    render,
    request_info,
    require_confirmation,
    require_human,
)
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.domain.wording import find_certainty_words
from cs_agent.policy.engine import PolicyOutcome, PolicyVerdict

FULL = TemplateVars(
    order_ref="82918",
    policy_id="REFUND-STD-001",
    policy_version=3,
    policy_summary="标准商品签收后 30 天内、未使用或未拆封可申请全额退款。",
    amount=Decimal("620.00"),
    max_auto_amount=Decimal("200"),
    missing_field="订单号",
)
EMPTY = TemplateVars()

#: 泄露性措辞：出现任何一个都说明模板在暗示"订单存在但不是你的"
#: （golden SEC-001/002/010、ORD-005 的 response_must_not_contain）。
LEAKY_WORDS = ("不属于你", "不属于您", "别人的", "其他用户", "他人", "确实不存在", "无权")


# --- 覆盖度 ---------------------------------------------------------------------


def test_every_reason_code_has_a_template() -> None:
    """20 个 reason_code 一个不落。"""
    assert {reason for _, reason in TEMPLATES} == set(ReasonCode)


def test_every_outcome_reason_pair_the_matrix_can_emit_has_a_template() -> None:
    """反过来从决策层出发：矩阵实际能产出的组合都要有骨架，不能运行到一半抛 KeyError。"""
    emitted: set[tuple[DecisionOutcome, ReasonCode]] = set()
    verdicts: list[PolicyVerdict | None] = [
        None,
        PolicyVerdict(
            outcome=PolicyOutcome.ALLOW,
            reason_code=ReasonCode.POLICY_SATISFIED,
            policy_id="REFUND-STD-001",
            policy_version=3,
            max_auto_amount=Decimal("200"),
        ),
        PolicyVerdict(
            outcome=PolicyOutcome.DENY,
            reason_code=ReasonCode.POLICY_VIOLATION_WINDOW,
            policy_id="REFUND-STD-001",
            policy_version=3,
        ),
        PolicyVerdict(
            outcome=PolicyOutcome.DENY,
            reason_code=ReasonCode.POLICY_VIOLATION_CATEGORY,
            policy_id="REFUND-FOOD-001",
            policy_version=2,
        ),
        PolicyVerdict(
            outcome=PolicyOutcome.DENY,
            reason_code=ReasonCode.POLICY_VIOLATION_CONDITION,
            policy_id="REFUND-STD-001",
            policy_version=3,
        ),
        PolicyVerdict(
            outcome=PolicyOutcome.REQUIRE_HUMAN,
            reason_code=ReasonCode.POLICY_AMBIGUOUS,
            policy_id="REFUND-UNDELIVERED-001",
            policy_version=1,
        ),
        PolicyVerdict(outcome=PolicyOutcome.NO_RULE, reason_code=ReasonCode.POLICY_AMBIGUOUS),
        PolicyVerdict(outcome=PolicyOutcome.AMBIGUOUS, reason_code=ReasonCode.POLICY_AMBIGUOUS),
    ]
    for flags in itertools.product([False, True], repeat=12):
        for verdict, score, amount in itertools.product(
            verdicts, [None, 0.05, 0.5, 0.95], [None, Decimal("100.00"), Decimal("620.00")]
        ):
            d = decide(
                DecisionInput(
                    ownership_ok=flags[0],
                    injection_suspected=flags[1],
                    role_sufficient=flags[2],
                    customer_requests_human=flags[3],
                    high_negative_sentiment=flags[4],
                    repeated_tool_failure=flags[5],
                    dependency_unavailable=flags[6],
                    verdict=verdict,
                    amount=amount,
                    is_write_intent=flags[7],
                    is_eligibility_intent=flags[8],
                    retrieval_max_score=score,
                    tau_low=0.30,
                    tau_high=0.80,
                    has_citable_chunk=flags[9],
                    idempotent_replay=flags[10],
                    missing_entity=flags[11],
                )
            )
            emitted.add((d.outcome, d.reason_code))
    assert emitted  # 保护：sweep 真的跑出了组合
    assert emitted <= set(TEMPLATES)
    for outcome, reason in sorted(emitted, key=str):
        render(Decision(outcome, reason, "x"), FULL)


def test_render_raises_on_an_unknown_pair() -> None:
    with pytest.raises(MissingTemplateError):
        render(Decision(DecisionOutcome.DENY, ReasonCode.OK, "x"), EMPTY)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    sorted(TEMPLATES, key=str),
    ids=[f"{o}/{r}" for o, r in sorted(TEMPLATES, key=str)],
)
@pytest.mark.parametrize("vars_", [FULL, EMPTY], ids=["full", "empty"])
def test_templates_are_total_and_deterministic(
    outcome: DecisionOutcome, reason: ReasonCode, vars_: TemplateVars
) -> None:
    """变量给全与全不给都要出得来文本，且两次调用逐字相同（纯函数）。"""
    text = render(Decision(outcome, reason, "x"), vars_)
    assert text == render(Decision(outcome, reason, "x"), vars_)
    # ANSWER/OK 是矩阵里唯一不受话术约束的分支，骨架刻意为空。
    if (outcome, reason) == (DecisionOutcome.ANSWER, ReasonCode.OK):
        assert text == ""
    else:
        assert text.strip()
        assert "None" not in text and "—" not in text  # 不渲染占位符
        assert "{" not in text and "}" not in text  # 没有漏填的槽位


# --- 不泄露存在性 ----------------------------------------------------------------


def test_ownership_denial_is_identical_for_existing_and_missing_orders() -> None:
    """契约 §2 行 77777、golden SEC-010：他人订单（90211）与不存在订单（77777）逐字相同。"""
    others = deny(ReasonCode.OWNERSHIP_MISMATCH, TemplateVars(order_ref="90211"))
    missing = deny(ReasonCode.OWNERSHIP_MISMATCH, TemplateVars(order_ref="77777"))
    assert others == missing == NOT_FOUND_TEXT


def test_ownership_denial_ignores_every_variable() -> None:
    """任何入参都不得改变归属类文案——包括金额、政策、缺失字段。"""
    loaded = deny(
        ReasonCode.OWNERSHIP_MISMATCH,
        TemplateVars(
            order_ref="90210",
            policy_id="REFUND-STD-001",
            policy_version=3,
            policy_summary="标准商品 30 天内可退。",
            amount=Decimal("199.00"),
            max_auto_amount=Decimal("200"),
            missing_field="订单号",
        ),
    )
    assert loaded == deny(ReasonCode.OWNERSHIP_MISMATCH, EMPTY) == NOT_FOUND_TEXT


def test_ownership_denial_leaks_nothing() -> None:
    for word in LEAKY_WORDS:
        assert word not in NOT_FOUND_TEXT
    # 契约 §1/§2：他人姓名与金额绝不出现
    for secret in ("陈静", "王芳", "59", "199", "90210", "90211", "77777"):
        assert secret not in NOT_FOUND_TEXT


@pytest.mark.parametrize(
    ("outcome", "reason"),
    sorted(TEMPLATES, key=str),
    ids=[f"{o}/{r}" for o, r in sorted(TEMPLATES, key=str)],
)
def test_no_template_hints_at_ownership(outcome: DecisionOutcome, reason: ReasonCode) -> None:
    """任何模板都不得暗示"这是别人的订单"。"""
    text = render(Decision(outcome, reason, "x"), FULL)
    assert not [w for w in LEAKY_WORDS if w in text]


# --- 低置信声明 ------------------------------------------------------------------


def test_low_confidence_disclosure_has_handoff_offer() -> None:
    """§9.4 规则 14 约束 2：低置信回答必须给出转人工入口。"""
    assert HANDOFF_OFFER in low_confidence_disclosure()


def test_low_confidence_disclosure_has_no_certainty_words() -> None:
    """对照 cs_agent.domain.wording 的词表，低置信文案不得说满话。"""
    assert find_certainty_words(low_confidence_disclosure()) == []


def test_low_confidence_disclosure_is_the_answer_branch_template() -> None:
    assert low_confidence_disclosure() == render(
        Decision(DecisionOutcome.ANSWER, ReasonCode.RETRIEVAL_LOW_CONFIDENCE, "14"), EMPTY
    )


@pytest.mark.parametrize(
    ("outcome", "reason"),
    sorted(TEMPLATES, key=str),
    ids=[f"{o}/{r}" for o, r in sorted(TEMPLATES, key=str)],
)
@pytest.mark.parametrize("vars_", [FULL, EMPTY], ids=["full", "empty"])
def test_no_template_uses_certainty_words(
    outcome: DecisionOutcome, reason: ReasonCode, vars_: TemplateVars
) -> None:
    """不止低置信：全部骨架都不说满话，避免"保证给你退"这类承诺进入回复。"""
    text = render(Decision(outcome, reason, "x"), vars_)
    assert find_certainty_words(text) == []


# --- 五个终态入口 ----------------------------------------------------------------


def test_escalation_templates_offer_a_human() -> None:
    """升级类文案要让用户知道人已经接上了，不能只说"不行"。"""
    for reason in (
        ReasonCode.CUSTOMER_ESCALATION_REQUEST,
        ReasonCode.HIGH_NEGATIVE_SENTIMENT,
        ReasonCode.TOOL_FAILURE_REPEATED,
        ReasonCode.TOOL_BUDGET_EXCEEDED,
        ReasonCode.POLICY_AMBIGUOUS,
        ReasonCode.LOW_CONFIDENCE_ON_DECISION,
        ReasonCode.RETRIEVAL_NO_RESULT,
        ReasonCode.DEPENDENCY_UNAVAILABLE,
    ):
        assert "人工" in require_human(reason, FULL)
    assert "人工审批" in require_human(ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT, FULL)


def test_deny_and_confirmation_cite_policy_id_and_version() -> None:
    """FR-306 / ADR-0006：引用的 id 与版本来自本轮判定，由调用方填入，模板照原样渲染。"""
    for reason in (
        ReasonCode.POLICY_VIOLATION_WINDOW,
        ReasonCode.POLICY_VIOLATION_CATEGORY,
        ReasonCode.POLICY_VIOLATION_CONDITION,
    ):
        text = deny(reason, FULL)
        assert "REFUND-STD-001" in text and "第 3 版" in text
    confirm = require_confirmation(FULL)
    assert "REFUND-STD-001" in confirm and "第 3 版" in confirm


def test_policy_basis_is_omitted_when_unknown() -> None:
    """没有 policy_id / version 就整段省略，不编造依据。"""
    text = deny(ReasonCode.POLICY_VIOLATION_WINDOW, TemplateVars(order_ref="82915"))
    assert "依据政策" not in text
    assert "82915" in text


def test_confirmation_asks_before_writing() -> None:
    """红线 2：写操作停在确认，文案里要有明确的确认动作。"""
    text = require_confirmation(FULL)
    assert "确认" in text
    assert "620.00" in text


def test_request_info_asks_for_the_missing_field_only() -> None:
    assert request_info(TemplateVars(missing_field="订单号")) == "为了帮你查询，请提供订单号。"
    assert request_info(EMPTY) == "为了帮你查询，请提供订单号。"


def test_degrade_says_partial_and_offers_handoff() -> None:
    text = degrade()
    assert "暂时" in text
    assert HANDOFF_OFFER in text


def test_deny_rejects_a_reason_code_from_another_outcome() -> None:
    """入口函数按终态收窄 reason_code，防止把升级文案当拒绝文案用。"""
    with pytest.raises(MissingTemplateError):
        deny(ReasonCode.CUSTOMER_ESCALATION_REQUEST, EMPTY)
    with pytest.raises(MissingTemplateError):
        require_human(ReasonCode.OWNERSHIP_MISMATCH, EMPTY)


# --- 英文骨架 -------------------------------------------------------------------
# 与中文一一对应，三条硬约束同样成立。语言由调用方按 domain/language 的确定性规则选。

from cs_agent.decision.templates import (  # noqa: E402
    HANDOFF_OFFER_EN,
    NOT_FOUND_TEXT_EN,
    TEMPLATES_BY_LANG,
    TEMPLATES_EN,
)
from cs_agent.domain.wording import find_certainty_words_en  # noqa: E402

LEAKY_WORDS_EN = (
    "not yours",
    "belongs to",
    "another user",
    "someone else",
    "does exist",
    "not authorized",
    "other customer",
)


def test_english_registry_covers_exactly_the_same_pairs() -> None:
    assert set(TEMPLATES_EN) == set(TEMPLATES)
    assert set(TEMPLATES_BY_LANG) == {"zh", "en"}


@pytest.mark.parametrize(
    ("outcome", "reason"),
    sorted(TEMPLATES_EN, key=str),
    ids=[f"{o}/{r}" for o, r in sorted(TEMPLATES_EN, key=str)],
)
@pytest.mark.parametrize("vars_", [FULL, EMPTY], ids=["full", "empty"])
def test_english_templates_are_total_clean_and_leak_free(
    outcome: DecisionOutcome, reason: ReasonCode, vars_: TemplateVars
) -> None:
    text = render(Decision(outcome, reason, "x"), vars_, lang="en")
    assert text == render(Decision(outcome, reason, "x"), vars_, lang="en")
    if (outcome, reason) == (DecisionOutcome.ANSWER, ReasonCode.OK):
        assert text == ""
        return
    assert text.strip()
    assert "None" not in text and "{" not in text and "}" not in text
    # 英文骨架里不该混进中文句子（策略摘要刻意不渲染）；「确认」是确认口令，允许
    assert not [ch for ch in text.replace("确认", "") if "一" <= ch <= "鿿"], text
    assert find_certainty_words_en(text) == []
    assert not [w for w in LEAKY_WORDS_EN if w in text.lower()]


def test_english_ownership_denial_is_identical_and_leaks_nothing() -> None:
    others = deny(ReasonCode.OWNERSHIP_MISMATCH, TemplateVars(order_ref="90211"), lang="en")
    missing = deny(ReasonCode.OWNERSHIP_MISMATCH, TemplateVars(order_ref="77777"), lang="en")
    assert (
        others
        == missing
        == NOT_FOUND_TEXT_EN
        == deny(ReasonCode.OWNERSHIP_MISMATCH, FULL, lang="en")
    )
    for secret in ("陈静", "王芳", "59", "199", "90210", "90211", "77777"):
        assert secret not in NOT_FOUND_TEXT_EN


def test_english_escalations_offer_or_state_a_human() -> None:
    for reason in (
        ReasonCode.CUSTOMER_ESCALATION_REQUEST,
        ReasonCode.HIGH_NEGATIVE_SENTIMENT,
        ReasonCode.TOOL_FAILURE_REPEATED,
        ReasonCode.TOOL_BUDGET_EXCEEDED,
        ReasonCode.POLICY_AMBIGUOUS,
        ReasonCode.LOW_CONFIDENCE_ON_DECISION,
        ReasonCode.RETRIEVAL_NO_RESULT,
        ReasonCode.DEPENDENCY_UNAVAILABLE,
    ):
        assert "human agent" in require_human(reason, FULL, lang="en")
    assert "approval" in require_human(ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT, FULL, lang="en")
    assert HANDOFF_OFFER_EN in low_confidence_disclosure("en")
    assert HANDOFF_OFFER_EN in degrade(lang="en")


def test_english_policy_basis_and_confirmation() -> None:
    for reason in (
        ReasonCode.POLICY_VIOLATION_WINDOW,
        ReasonCode.POLICY_VIOLATION_CATEGORY,
        ReasonCode.POLICY_VIOLATION_CONDITION,
    ):
        text = deny(reason, FULL, lang="en")
        assert "REFUND-STD-001" in text and "version 3" in text
    assert "per policy" not in deny(
        ReasonCode.POLICY_VIOLATION_WINDOW, TemplateVars(order_ref="82915"), lang="en"
    )
    confirm = require_confirmation(FULL, lang="en")
    assert "REFUND-STD-001" in confirm and "620.00" in confirm and "确认" in confirm


def test_english_request_info_maps_the_field_name() -> None:
    assert request_info(TemplateVars(missing_field="订单号"), lang="en") == (
        "To look this up, please provide the order number."
    )
    assert "ticket number" in request_info(TemplateVars(missing_field="工单号"), lang="en")
    assert request_info(EMPTY, lang="en") == request_info(
        TemplateVars(missing_field="订单号"), lang="en"
    )


def test_default_language_is_chinese() -> None:
    assert render(Decision(DecisionOutcome.REQUEST_INFO, ReasonCode.MISSING_ENTITY, "x")) == (
        request_info(EMPTY)
    )
