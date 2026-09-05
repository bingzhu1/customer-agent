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
   `cs_agent.eval.wording.CERTAINTY_WORDS`），并且必须带 `handoff_offer`（§9.4 规则 14 约束 2）。
   本模块不 import eval，词表约束由 `tests/test_templates.py` 反向校验。
3. **引用与判定同源**：涉及政策的文案里的 `policy_id` / `policy_version` 由调用方从
   本轮 `PolicyVerdict` 取，不另行检索，保证 FR-306 的引用—执行一致（ADR-0006）。

模块不做 IO、不调 LLM、不读记忆。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from cs_agent.decision.matrix import Decision
from cs_agent.domain.enums import DecisionOutcome, ReasonCode

#: 转人工入口。低置信回答与多数升级文案都要带上（§9.4 规则 14 约束 2）。
HANDOFF_OFFER = "如果需要，我可以为你转接人工客服。"

#: 归属不符 / 查不到对象时的唯一文案。常量而非模板，杜绝任何随入参变化的可能。
NOT_FOUND_TEXT = "抱歉，我没有查到这个订单。请核对订单号后再试。" + HANDOFF_OFFER


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


class MissingTemplateError(KeyError):
    """矩阵产出了没有对应模板的 (终态, reason_code)。属于代码缺陷，不是运行期分支。"""


# --- 五个终态的对外入口 -----------------------------------------------------------


def _lookup(outcome: DecisionOutcome, reason_code: ReasonCode, vars_: TemplateVars) -> str:
    try:
        template = TEMPLATES[(outcome, reason_code)]
    except KeyError as exc:
        raise MissingTemplateError(f"no template for {outcome}/{reason_code}") from exc
    return template(vars_)


def deny(reason_code: ReasonCode, vars_: TemplateVars = NO_VARS) -> str:
    """DENY 文案。归属类一律返回统一的 not_found 文本，不区分"不存在"与"不属于你"。"""
    return _lookup(DecisionOutcome.DENY, reason_code, vars_)


def require_human(reason_code: ReasonCode, vars_: TemplateVars = NO_VARS) -> str:
    """REQUIRE_HUMAN 文案。都带明确的"已转接"表述，不留悬念。"""
    return _lookup(DecisionOutcome.REQUIRE_HUMAN, reason_code, vars_)


def request_info(vars_: TemplateVars = NO_VARS) -> str:
    """REQUEST_INFO 文案。只索取缺失的那一个实体。"""
    return _lookup(DecisionOutcome.REQUEST_INFO, ReasonCode.MISSING_ENTITY, vars_)


def require_confirmation(vars_: TemplateVars = NO_VARS) -> str:
    """REQUIRE_CONFIRMATION 文案。写操作在此停下，等用户确认（红线 2）。"""
    return _lookup(DecisionOutcome.REQUIRE_CONFIRMATION, ReasonCode.POLICY_SATISFIED, vars_)


def degrade(vars_: TemplateVars = NO_VARS) -> str:
    """DEGRADE 文案。说明哪部分不可用，并给出转人工入口。"""
    return _lookup(DecisionOutcome.DEGRADE, ReasonCode.DEPENDENCY_UNAVAILABLE, vars_)


def low_confidence_disclosure() -> str:
    """FR-308 的低置信声明（含 handoff_offer）。由 `respond` 节点拼在回答正文之前。"""
    return _answer_low_confidence(NO_VARS)


def render(decision: Decision, vars_: TemplateVars = NO_VARS) -> str:
    """按 `Decision` 取骨架。`ANSWER` / `OK` 返回空串，表示该分支不受话术约束。"""
    return _lookup(decision.outcome, decision.reason_code, vars_)
