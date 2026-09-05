"""受约束的拒绝 / 升级话术骨架（PRD FR-407、FR-308、§9.4 "用户看到"列）。

**LLM 不参与本模块的任何一个字。** 每个模板是纯函数，只接受结构化变量
（订单号、policy_id / policy_version、human_text 摘要、金额），拼接出确定的中文文本。
`respond` 节点只能从这里取骨架，不允许自行组织拒绝理由或升级说辞——否则
"为什么被拒"的口径会随模型输出漂移，评估也就没有可断言的对象。

三条硬约束：

1. **不泄露存在性**：`OWNERSHIP_MISMATCH` 的文案对"他人的订单"与"不存在的订单"
   必须逐字相同。因此该模板**刻意不回显订单号**，也不接受"是否存在"这类入参——
   结构上就写不出会泄露的分支（契约 §2 行 77777、golden SEC-010 / ORD-005）。
2. **低置信不得说满话**：低置信声明模板不出现确定性措辞（对照
   `cs_agent.domain.wording.CERTAINTY_WORDS`），并且必须带 `handoff_offer`（§9.4 规则 14 约束 2）。
   词表本体在 `domain/wording.py`，由 `tests/test_templates.py` 反向校验；
   本模块自身不 import 词表，模板文本不依赖任何检查器。
3. **引用与判定同源**：涉及政策的文案里的 `policy_id` / `policy_version` 由调用方从
   本轮 `PolicyVerdict` 取，不另行检索，保证 FR-306 的引用—执行一致（ADR-0006）。

**语言**：每个 (终态, reason_code) 有中文与英文两套骨架，内容逐句对应，
由 `lang` 参数选择。语言由调用方按 `cs_agent.domain.language` 的确定性规则给出
（本轮要求 → 记忆偏好 → 中文）；模板本身仍不读记忆、不调 LLM。
英文版**不渲染** `policy_summary`（策略正文只有中文），只保留 policy_id 与版本。

模块不做 IO、不调 LLM、不读记忆。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from cs_agent.decision.matrix import Decision
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.domain.language import DEFAULT_LANG, Lang

#: 转人工入口。低置信回答与多数升级文案都要带上（§9.4 规则 14 约束 2）。
HANDOFF_OFFER = "如果需要，我可以为你转接人工客服。"

#: 归属不符 / 查不到对象时的唯一文案。常量而非模板，杜绝任何随入参变化的可能。
NOT_FOUND_TEXT = "抱歉，我没有查到这个订单。请核对订单号后再试。" + HANDOFF_OFFER

HANDOFF_OFFER_EN = " If you'd like, I can transfer you to a human agent."
NOT_FOUND_TEXT_EN = (
    "Sorry, I couldn't find that order. Please check the order number and try again."
    + HANDOFF_OFFER_EN
)


@dataclass(frozen=True, slots=True)
class TemplateVars:
    """模板允许填充的全部变量。都是结构化值，由确定性代码从业务库与判定结果取。

    不接受自由文本：`policy_summary` 是对策略 `human_text` 的摘要，由调用方按
    确定规则截取（如首句），不是 LLM 生成的转述。
    """

    order_ref: str | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    policy_summary: str | None = None
    amount: Decimal | None = None
    max_auto_amount: Decimal | None = None
    #: 缺失实体的中文名，如"订单号"。
    missing_field: str | None = None


#: 不带任何变量的默认入参。冻结不可变，可安全用作默认值。
NO_VARS = TemplateVars()


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _order_phrase(vars_: TemplateVars) -> str:
    """订单指代。带号码时末尾留一个空格，让"订单 82918 的退款"排版正常；
    不带号码时不留，得到"该订单的退款"。两种情况后面都直接接汉字。"""
    return "该订单" if vars_.order_ref is None else f"订单 {vars_.order_ref} "


def _basis(vars_: TemplateVars) -> str:
    """ "（依据政策 X 第 N 版）"。缺 id 或版本时整段省略，绝不编造。"""
    if vars_.policy_id is None or vars_.policy_version is None:
        return ""
    return f"（依据政策 {vars_.policy_id} 第 {vars_.policy_version} 版）"


def _summary(vars_: TemplateVars) -> str:
    return "" if not vars_.policy_summary else f"{vars_.policy_summary}"


# --- DENY -----------------------------------------------------------------------


def _deny_not_found(vars_: TemplateVars) -> str:
    # 入参一律忽略：这正是"逐字相同"的实现方式。
    del vars_
    return NOT_FOUND_TEXT


def _deny_injection(vars_: TemplateVars) -> str:
    del vars_
    return (
        "抱歉，这个请求我无法处理。"
        "如果你有订单查询或售后方面的问题，可以直接告诉我，我会为你查。" + HANDOFF_OFFER
    )


def _deny_auth(vars_: TemplateVars) -> str:
    del vars_
    return "抱歉，当前账号的权限不足以完成这个操作。" + HANDOFF_OFFER


def _deny_policy(headline: str) -> Callable[[TemplateVars], str]:
    def render(vars_: TemplateVars) -> str:
        parts = [f"抱歉，{_order_phrase(vars_)}的退款无法办理：{headline}。"]
        summary = _summary(vars_)
        if summary:
            parts.append(summary)
        basis = _basis(vars_)
        if basis:
            parts.append(basis)
        return "".join(parts) + HANDOFF_OFFER

    return render


# --- REQUIRE_HUMAN --------------------------------------------------------------


def _human_customer_request(vars_: TemplateVars) -> str:
    del vars_
    return "好的，正在为你转接人工客服，请稍等。"


def _human_sentiment(vars_: TemplateVars) -> str:
    del vars_
    return "很抱歉这次的体验让你不满意。我已经为你转接人工客服，会有专员尽快跟进。"


def _human_tool_failure(vars_: TemplateVars) -> str:
    del vars_
    return "抱歉，系统暂时没能完成查询。我已经为你转接人工客服，请稍等。"


def _human_tool_budget(vars_: TemplateVars) -> str:
    del vars_
    return "这个问题查询步骤较多，为了不让你久等，我已经为你转接人工客服。"


def _human_ambiguous(vars_: TemplateVars) -> str:
    return (
        f"{_order_phrase(vars_)}的退款需要人工核实适用政策{_basis(vars_)}，我已经为你转接人工客服。"
    )


def _human_amount(vars_: TemplateVars) -> str:
    # 金额缺失时整句降级为不含数字的表述，绝不渲染占位符。
    if vars_.amount is None or vars_.max_auto_amount is None:
        head = f"{_order_phrase(vars_)}的退款金额超出自动处理上限，需要人工审批。"
    else:
        head = (
            f"{_order_phrase(vars_)}的退款金额为 {_money(vars_.amount)} 元，"
            f"超出 {_money(vars_.max_auto_amount)} 元的自动处理上限，需要人工审批。"
        )
    return head + _basis(vars_) + "我已经提交审批，会有专员跟进处理。"


def _human_low_confidence(vars_: TemplateVars) -> str:
    del vars_
    return "这个问题涉及退款资格判断，我掌握的政策依据还不够充分，已经为你转接人工客服。"


def _human_no_result(vars_: TemplateVars) -> str:
    del vars_
    return "抱歉，我没有查到与这个问题相关的政策说明，已经为你转接人工客服。"


def _human_dependency(vars_: TemplateVars) -> str:
    del vars_
    return "抱歉，需要用到的系统暂时不可用，我已经为你转接人工客服。"


# --- REQUEST_INFO / REQUIRE_CONFIRMATION / DEGRADE -------------------------------


def _request_info(vars_: TemplateVars) -> str:
    field = vars_.missing_field or "订单号"
    return f"为了帮你查询，请提供{field}。"


def _require_confirmation(vars_: TemplateVars) -> str:
    parts = [f"{_order_phrase(vars_)}符合退款条件。"]
    summary = _summary(vars_)
    if summary:
        parts.append(summary)
    basis = _basis(vars_)
    if basis:
        parts.append(basis)
    if vars_.amount is None:
        parts.append("本次退款将原路退回你的支付账户。")
    else:
        parts.append(f"本次退款金额 {_money(vars_.amount)} 元，将原路退回你的支付账户。")
    parts.append("确认提交请回复「确认」，需要修改请直接告诉我。")
    return "".join(parts)


def _degrade(vars_: TemplateVars) -> str:
    del vars_
    return (
        "抱歉，部分信息暂时查询不到，相关服务正在恢复中。"
        "我先把已经拿到的部分告诉你，稍后你可以再问一次。" + HANDOFF_OFFER
    )


# --- ANSWER ---------------------------------------------------------------------


def _answer_ok(vars_: TemplateVars) -> str:
    """`OK` 是矩阵里唯一不受话术约束的分支：正文由 `respond` 节点生成，骨架为空。"""
    del vars_
    return ""


def _answer_replay(vars_: TemplateVars) -> str:
    return f"{_order_phrase(vars_)}的这笔退款此前已经处理过，下面是当时的结果，系统不会重复退款。"


def _answer_low_confidence(vars_: TemplateVars) -> str:
    """FR-308：低置信声明。不出现确定性措辞，且带转人工入口。"""
    del vars_
    return (
        "以下回答依据我检索到的政策条款，但相关度不高，可能不完全适用于你的情况，"
        "请以人工客服的答复为准。" + HANDOFF_OFFER
    )


# --- 注册表 ---------------------------------------------------------------------

#: (终态, reason_code) → 模板函数。`DEPENDENCY_UNAVAILABLE` 在 DEGRADE 与
#: REQUIRE_HUMAN 下文案不同，所以键是二元组而不是单个 reason_code。
TEMPLATES: dict[tuple[DecisionOutcome, ReasonCode], Callable[[TemplateVars], str]] = {
    (DecisionOutcome.DENY, ReasonCode.OWNERSHIP_MISMATCH): _deny_not_found,
    (DecisionOutcome.DENY, ReasonCode.SUSPECTED_INJECTION): _deny_injection,
    (DecisionOutcome.DENY, ReasonCode.AUTH_INSUFFICIENT): _deny_auth,
    (DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_WINDOW): _deny_policy("已超出退款时限"),
    (DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_CATEGORY): _deny_policy(
        "该商品类别不支持退款"
    ),
    (DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_CONDITION): _deny_policy(
        "商品当前状态不符合退款要求"
    ),
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.CUSTOMER_ESCALATION_REQUEST): (
        _human_customer_request
    ),
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.HIGH_NEGATIVE_SENTIMENT): _human_sentiment,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.TOOL_FAILURE_REPEATED): _human_tool_failure,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.TOOL_BUDGET_EXCEEDED): _human_tool_budget,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.POLICY_AMBIGUOUS): _human_ambiguous,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT): _human_amount,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.LOW_CONFIDENCE_ON_DECISION): _human_low_confidence,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.RETRIEVAL_NO_RESULT): _human_no_result,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.DEPENDENCY_UNAVAILABLE): _human_dependency,
    (DecisionOutcome.REQUEST_INFO, ReasonCode.MISSING_ENTITY): _request_info,
    (DecisionOutcome.REQUIRE_CONFIRMATION, ReasonCode.POLICY_SATISFIED): _require_confirmation,
    (DecisionOutcome.DEGRADE, ReasonCode.DEPENDENCY_UNAVAILABLE): _degrade,
    (DecisionOutcome.ANSWER, ReasonCode.OK): _answer_ok,
    (DecisionOutcome.ANSWER, ReasonCode.IDEMPOTENT_REPLAY): _answer_replay,
    (DecisionOutcome.ANSWER, ReasonCode.RETRIEVAL_LOW_CONFIDENCE): _answer_low_confidence,
    # POLICY_SATISFIED 也可能随信息类回答出现（"你这单是符合条件的"），骨架同确认文案。
    (DecisionOutcome.ANSWER, ReasonCode.POLICY_SATISFIED): _require_confirmation,
}

# --- English --------------------------------------------------------------------
# 与中文骨架一一对应；约束相同：归属类逐字相同、不说满话、不编依据。


def _order_phrase_en(vars_: TemplateVars) -> str:
    return "this order" if vars_.order_ref is None else f"order {vars_.order_ref}"


def _basis_en(vars_: TemplateVars) -> str:
    if vars_.policy_id is None or vars_.policy_version is None:
        return ""
    return f" (per policy {vars_.policy_id}, version {vars_.policy_version})"


_MISSING_FIELD_EN: dict[str, str] = {"订单号": "the order number", "工单号": "the ticket number"}


def _deny_not_found_en(vars_: TemplateVars) -> str:
    del vars_
    return NOT_FOUND_TEXT_EN


def _deny_injection_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "Sorry, I can't process that request. "
        "If you have a question about an order or after-sales service, just tell me and "
        "I'll look it up." + HANDOFF_OFFER_EN
    )


def _deny_auth_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "Sorry, this account doesn't have sufficient permissions for that operation."
        + HANDOFF_OFFER_EN
    )


def _deny_policy_en(headline: str) -> Callable[[TemplateVars], str]:
    def render(vars_: TemplateVars) -> str:
        return (
            f"Sorry, a refund for {_order_phrase_en(vars_)} can't be processed: {headline}"
            f"{_basis_en(vars_)}." + HANDOFF_OFFER_EN
        )

    return render


def _human_customer_request_en(vars_: TemplateVars) -> str:
    del vars_
    return "Sure, I'm transferring you to a human agent now. Please hold on."


def _human_sentiment_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "I'm sorry this experience has been frustrating. I've transferred you to a human agent, "
        "and a specialist will follow up shortly."
    )


def _human_tool_failure_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "Sorry, the system couldn't complete the lookup just now. "
        "I've transferred you to a human agent. Please hold on."
    )


def _human_tool_budget_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "This question takes several lookup steps. To save you the wait, "
        "I've transferred you to a human agent."
    )


def _human_ambiguous_en(vars_: TemplateVars) -> str:
    return (
        f"The refund for {_order_phrase_en(vars_)} needs a human agent to confirm which policy "
        f"applies{_basis_en(vars_)}. I've transferred you to a human agent."
    )


def _human_amount_en(vars_: TemplateVars) -> str:
    if vars_.amount is None or vars_.max_auto_amount is None:
        head = (
            f"The refund amount for {_order_phrase_en(vars_)} exceeds the automatic processing "
            "limit and needs manual approval"
        )
    else:
        head = (
            f"The refund for {_order_phrase_en(vars_)} is ¥{_money(vars_.amount)}, which exceeds "
            f"the ¥{_money(vars_.max_auto_amount)} automatic processing limit and needs manual "
            "approval"
        )
    return (
        head + _basis_en(vars_) + ". I've submitted it for approval; a specialist will follow up."
    )


def _human_low_confidence_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "This involves a refund eligibility decision and the policy basis I have isn't sufficient, "
        "so I've transferred you to a human agent."
    )


def _human_no_result_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "Sorry, I couldn't find a policy that covers this question, "
        "so I've transferred you to a human agent."
    )


def _human_dependency_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "Sorry, a system I need is temporarily unavailable. I've transferred you to a human agent."
    )


def _request_info_en(vars_: TemplateVars) -> str:
    field = vars_.missing_field or "订单号"
    return f"To look this up, please provide {_MISSING_FIELD_EN.get(field, field)}."


def _require_confirmation_en(vars_: TemplateVars) -> str:
    head = f"{_order_phrase_en(vars_).capitalize()} is eligible for a refund{_basis_en(vars_)}."
    if vars_.amount is None:
        money = " The refund will go back to your original payment method."
    else:
        money = (
            f" The refund of ¥{_money(vars_.amount)} will go back to your original payment method."
        )
    return head + money + ' To submit, reply "确认" (confirm); to change anything, just tell me.'


def _degrade_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "Sorry, some information is temporarily unavailable while the service recovers. "
        "Here's what I could retrieve so far; you can ask again a little later." + HANDOFF_OFFER_EN
    )


def _answer_replay_en(vars_: TemplateVars) -> str:
    return (
        f"The refund for {_order_phrase_en(vars_)} was already processed earlier. "
        "Here is the original result; the system will not refund it again."
    )


def _answer_low_confidence_en(vars_: TemplateVars) -> str:
    del vars_
    return (
        "The following answer is based on policy clauses I retrieved, but their relevance is low "
        "and they may not fully apply to your situation. Please treat the human agent's reply as "
        "authoritative." + HANDOFF_OFFER_EN
    )


TEMPLATES_EN: dict[tuple[DecisionOutcome, ReasonCode], Callable[[TemplateVars], str]] = {
    (DecisionOutcome.DENY, ReasonCode.OWNERSHIP_MISMATCH): _deny_not_found_en,
    (DecisionOutcome.DENY, ReasonCode.SUSPECTED_INJECTION): _deny_injection_en,
    (DecisionOutcome.DENY, ReasonCode.AUTH_INSUFFICIENT): _deny_auth_en,
    (DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_WINDOW): _deny_policy_en(
        "the refund window has passed"
    ),
    (DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_CATEGORY): _deny_policy_en(
        "this product category is not refundable"
    ),
    (DecisionOutcome.DENY, ReasonCode.POLICY_VIOLATION_CONDITION): _deny_policy_en(
        "the item's current condition doesn't meet the refund requirements"
    ),
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.CUSTOMER_ESCALATION_REQUEST): (
        _human_customer_request_en
    ),
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.HIGH_NEGATIVE_SENTIMENT): _human_sentiment_en,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.TOOL_FAILURE_REPEATED): _human_tool_failure_en,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.TOOL_BUDGET_EXCEEDED): _human_tool_budget_en,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.POLICY_AMBIGUOUS): _human_ambiguous_en,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT): _human_amount_en,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.LOW_CONFIDENCE_ON_DECISION): (
        _human_low_confidence_en
    ),
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.RETRIEVAL_NO_RESULT): _human_no_result_en,
    (DecisionOutcome.REQUIRE_HUMAN, ReasonCode.DEPENDENCY_UNAVAILABLE): _human_dependency_en,
    (DecisionOutcome.REQUEST_INFO, ReasonCode.MISSING_ENTITY): _request_info_en,
    (DecisionOutcome.REQUIRE_CONFIRMATION, ReasonCode.POLICY_SATISFIED): _require_confirmation_en,
    (DecisionOutcome.DEGRADE, ReasonCode.DEPENDENCY_UNAVAILABLE): _degrade_en,
    (DecisionOutcome.ANSWER, ReasonCode.OK): _answer_ok,
    (DecisionOutcome.ANSWER, ReasonCode.IDEMPOTENT_REPLAY): _answer_replay_en,
    (DecisionOutcome.ANSWER, ReasonCode.RETRIEVAL_LOW_CONFIDENCE): _answer_low_confidence_en,
    (DecisionOutcome.ANSWER, ReasonCode.POLICY_SATISFIED): _require_confirmation_en,
}

#: 语言 → 注册表。两张表的键集合必须相同（tests/test_templates.py 校验）。
TEMPLATES_BY_LANG: dict[
    Lang, dict[tuple[DecisionOutcome, ReasonCode], Callable[[TemplateVars], str]]
] = {
    "zh": TEMPLATES,
    "en": TEMPLATES_EN,
}


class MissingTemplateError(KeyError):
    """矩阵产出了没有对应模板的 (终态, reason_code)。属于代码缺陷，不是运行期分支。"""


# --- 五个终态的对外入口 -----------------------------------------------------------


def _lookup(
    outcome: DecisionOutcome,
    reason_code: ReasonCode,
    vars_: TemplateVars,
    lang: Lang = DEFAULT_LANG,
) -> str:
    try:
        template = TEMPLATES_BY_LANG[lang][(outcome, reason_code)]
    except KeyError as exc:
        raise MissingTemplateError(f"no template for {outcome}/{reason_code} ({lang})") from exc
    return template(vars_)


def deny(
    reason_code: ReasonCode, vars_: TemplateVars = NO_VARS, *, lang: Lang = DEFAULT_LANG
) -> str:
    """DENY 文案。归属类一律返回统一的 not_found 文本，不区分"不存在"与"不属于你"。"""
    return _lookup(DecisionOutcome.DENY, reason_code, vars_, lang)


def require_human(
    reason_code: ReasonCode, vars_: TemplateVars = NO_VARS, *, lang: Lang = DEFAULT_LANG
) -> str:
    """REQUIRE_HUMAN 文案。都带明确的"已转接"表述，不留悬念。"""
    return _lookup(DecisionOutcome.REQUIRE_HUMAN, reason_code, vars_, lang)


def request_info(vars_: TemplateVars = NO_VARS, *, lang: Lang = DEFAULT_LANG) -> str:
    """REQUEST_INFO 文案。只索取缺失的那一个实体。"""
    return _lookup(DecisionOutcome.REQUEST_INFO, ReasonCode.MISSING_ENTITY, vars_, lang)


def require_confirmation(vars_: TemplateVars = NO_VARS, *, lang: Lang = DEFAULT_LANG) -> str:
    """REQUIRE_CONFIRMATION 文案。写操作在此停下，等用户确认（红线 2）。"""
    return _lookup(DecisionOutcome.REQUIRE_CONFIRMATION, ReasonCode.POLICY_SATISFIED, vars_, lang)


def degrade(vars_: TemplateVars = NO_VARS, *, lang: Lang = DEFAULT_LANG) -> str:
    """DEGRADE 文案。说明哪部分不可用，并给出转人工入口。"""
    return _lookup(DecisionOutcome.DEGRADE, ReasonCode.DEPENDENCY_UNAVAILABLE, vars_, lang)


def low_confidence_disclosure(lang: Lang = DEFAULT_LANG) -> str:
    """FR-308 的低置信声明（含 handoff_offer）。由 `respond` 节点拼在回答正文之前。"""
    return _lookup(DecisionOutcome.ANSWER, ReasonCode.RETRIEVAL_LOW_CONFIDENCE, NO_VARS, lang)


def render(decision: Decision, vars_: TemplateVars = NO_VARS, *, lang: Lang = DEFAULT_LANG) -> str:
    """按 `Decision` 与语言取骨架。`ANSWER` / `OK` 返回空串，表示该分支不受话术约束。"""
    return _lookup(decision.outcome, decision.reason_code, vars_, lang)
