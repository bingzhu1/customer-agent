"""决策层：PRD §9.4 的升级矩阵，确定性有序规则表（FR-404/405/406）。

纯函数 `decide(DecisionInput) -> Decision`。16 条规则（含 6b、10.5、14b 共 19 个分支）
**按序求值、首次命中即返回**，返回值回带命中的规则编号 `rule_no` 供审计与排障。

规则 6b 是本实现相对 §9.4 原表的一处补充：`TOOL_BUDGET_EXCEEDED`（FR-210）原本没有
对应行，图里靠事后钳位实现。钳位读不出"为什么"，也无法被矩阵测试覆盖，因此提为正式一行。

设计约束：

- 本层不调用 LLM，不做 IO，不读记忆。所有输入都由确定性代码在上游算好；
- `verdict` 是策略引擎的输出，本层只做翻译与优先级归并，不重新解释政策；
- 升级逻辑集中在这一张表里，不允许散落到 prompt 或各节点（PRD §9.6 最后一行）。

顺序上的三个要点：

- 规则 1–3 是安全闸门，压过一切。归属不符时连"订单存在与否"都不能泄露；
- 规则 10.5 压过 12：只要本轮牵涉写操作或资格判定，检索置信度不足一律转人工，
  不允许低置信下走到"请用户确认"（PRD §9.4 规则 14 的约束 1）；
- 规则 14 必须有可引用 chunk，否则退回 14b 转人工（约束 3）——低置信不等于允许无据回答。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.policy.engine import PolicyOutcome, PolicyVerdict


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """矩阵求值所需的全部信号。字段全部由确定性代码填充，不接受记忆类输入（红线 3）。"""

    #: 请求对象是否属于当前用户（Repository 层强制 scope 的结果）。
    ownership_ok: bool = True
    #: 是否检测到注入特征（用户消息或工具输出中的指令性内容）。
    injection_suspected: bool = False
    #: 当前 AuthContext 的角色是否足以执行本次意图。
    role_sufficient: bool = True
    customer_requests_human: bool = False
    high_negative_sentiment: bool = False
    #: 同一工具连续失败 ≥ 2 次。
    repeated_tool_failure: bool = False
    #: 单轮工具调用次数超预算（FR-210）。超预算强制进人工，不允许比矩阵结论更宽松。
    tool_budget_exceeded: bool = False
    dependency_unavailable: bool = False
    #: 策略引擎的判定结果；本轮不涉及策略判定时为 None。
    verdict: PolicyVerdict | None = None
    #: 本次写操作的金额，来自业务库，不来自对话。
    amount: Decimal | None = None
    is_write_intent: bool = False
    is_eligibility_intent: bool = False
    #: 本轮检索的最高相似度；未做检索时为 None。
    retrieval_max_score: float | None = None
    tau_low: float = 0.0
    tau_high: float = 1.0
    has_citable_chunk: bool = False
    #: 已存在同幂等键的成功动作。
    idempotent_replay: bool = False
    missing_entity: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    """终态。`rule_no` 是 §9.4 表中的行号，如 "1" / "10.5" / "14b"。"""

    outcome: DecisionOutcome
    reason_code: ReasonCode
    rule_no: str


def decide(inp: DecisionInput) -> Decision:
    """按 §9.4 顺序求值，首次命中即返回。纯函数。"""
    verdict = inp.verdict
    decisional = inp.is_write_intent or inp.is_eligibility_intent

    # 1 请求对象不属于当前用户
    if not inp.ownership_ok:
        return Decision(DecisionOutcome.DENY, ReasonCode.OWNERSHIP_MISMATCH, "1")

    # 2 检测到注入特征 / 工具输出含指令
    if inp.injection_suspected:
        return Decision(DecisionOutcome.DENY, ReasonCode.SUSPECTED_INJECTION, "2")

    # 3 角色权限不足
    if not inp.role_sufficient:
        return Decision(DecisionOutcome.DENY, ReasonCode.AUTH_INSUFFICIENT, "3")

    # 4 用户明确要求人工
    if inp.customer_requests_human:
        return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.CUSTOMER_ESCALATION_REQUEST, "4")

    # 5 强负面情绪 / 投诉升级信号
    if inp.high_negative_sentiment:
        return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.HIGH_NEGATIVE_SENTIMENT, "5")

    # 6 同一工具连续失败 ≥ 2 次
    if inp.repeated_tool_failure:
        return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.TOOL_FAILURE_REPEATED, "6")

    # 6b 单轮工具预算耗尽（FR-210）。§9.4 原表没有这一行，补在 6 之后、7 之前：
    #     它与规则 6 同属"系统侧已经尽力但没拿到结果"，都该在策略判定之前转人工。
    if inp.tool_budget_exceeded:
        return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.TOOL_BUDGET_EXCEEDED, "6b")

    # 7 关键依赖不可用
    if inp.dependency_unavailable:
        return Decision(DecisionOutcome.DEGRADE, ReasonCode.DEPENDENCY_UNAVAILABLE, "7")

    # 8 PolicyVerdict = DENY
    if verdict is not None and verdict.outcome is PolicyOutcome.DENY:
        return Decision(DecisionOutcome.DENY, verdict.reason_code, "8")

    # 9 无匹配规则 / 规则冲突 / 政策要求人工（如未签收订单拦截件）
    if verdict is not None and verdict.outcome in (
        PolicyOutcome.NO_RULE,
        PolicyOutcome.AMBIGUOUS,
        PolicyOutcome.REQUIRE_HUMAN,
    ):
        return Decision(DecisionOutcome.REQUIRE_HUMAN, verdict.reason_code, "9")
    if verdict is None and decisional:
        # 本轮要判资格却拿不到判定结果，同样按政策歧义转人工，不许默认放行。
        return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.POLICY_AMBIGUOUS, "9")

    # 10 金额超过自动处理上限
    if (
        verdict is not None
        and verdict.outcome is PolicyOutcome.ALLOW
        and verdict.max_auto_amount is not None
        and inp.amount is not None
        and inp.amount > verdict.max_auto_amount
    ):
        return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT, "10")

    # 10.5 涉及写操作或资格判定，且检索置信度低于 τ_high
    if (
        decisional
        and inp.retrieval_max_score is not None
        and inp.retrieval_max_score < inp.tau_high
    ):
        return Decision(
            DecisionOutcome.REQUIRE_HUMAN, ReasonCode.LOW_CONFIDENCE_ON_DECISION, "10.5"
        )

    # 11 已存在同幂等键的成功动作
    if inp.idempotent_replay:
        return Decision(DecisionOutcome.ANSWER, ReasonCode.IDEMPOTENT_REPLAY, "11")

    # 12 策略通过且是写操作
    if verdict is not None and verdict.outcome is PolicyOutcome.ALLOW and inp.is_write_intent:
        return Decision(DecisionOutcome.REQUIRE_CONFIRMATION, verdict.reason_code, "12")

    # 13 / 14 / 14b 只适用于纯信息类问答（规则 14 的约束 1）
    if not decisional and inp.retrieval_max_score is not None:
        if inp.retrieval_max_score < inp.tau_low:
            return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.RETRIEVAL_NO_RESULT, "13")
        if inp.retrieval_max_score < inp.tau_high:
            if inp.has_citable_chunk:
                return Decision(DecisionOutcome.ANSWER, ReasonCode.RETRIEVAL_LOW_CONFIDENCE, "14")
            return Decision(DecisionOutcome.REQUIRE_HUMAN, ReasonCode.RETRIEVAL_NO_RESULT, "14b")

    # 15 缺少必需实体
    if inp.missing_entity:
        return Decision(DecisionOutcome.REQUEST_INFO, ReasonCode.MISSING_ENTITY, "15")

    # 16 其他
    return Decision(DecisionOutcome.ANSWER, ReasonCode.OK, "16")
