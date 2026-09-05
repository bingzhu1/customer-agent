"""确定性策略引擎（PRD §9.2、FR-401/402/408，契约 §8）。

纯函数：`evaluate(facts, rules) -> PolicyVerdict`。无 IO、无 LLM、无时钟、无随机。
只读 `PolicyFacts` 与 `PolicySet`，同样的输入永远得到同样的输出。

判定流程：

1. 只看 `effect` 为 `allow_refund` / `deny_refund` / `require_human` 的规则；
   `informational` 规则只用于 RAG 回答，**永不参与资格判定**（契约 §8 第 4 行）。
2. `applies_to` 按 `item_category` / `user_tier` 过滤（字符串或列表，缺省表示不限制）。
   `applies_to.ticket_type` 不在 `PolicyFacts` 里，本引擎忽略它——工单类型不是退款资格的输入。
3. 剩下的规则逐条求值 `conditions`（schema 的 8 个操作符，同一字段多个操作符取 AND）。
4. 按下面的优先级归并成唯一 `PolicyVerdict`。

优先级（安全优先，越保守越靠前）：

| 顺序 | 命中情况 | outcome | reason_code |
|---|---|---|---|
| 1 | `require_human` 规则条件通过 | `REQUIRE_HUMAN` | 规则的 `reason_code_on_pass` |
| 2 | `deny_refund` 规则条件通过 | `DENY` | 规则的 `reason_code_on_pass` |
| 3 | 多条 `allow_refund` 同时通过 | `AMBIGUOUS` | `POLICY_AMBIGUOUS` |
| 4 | 恰好一条 `allow_refund` 通过 | `ALLOW` | 规则的 `reason_code_on_pass` |
| 5 | 有 `allow_refund` 适用但条件不通过 | `DENY` | `fail_reason_codes` 或 `reason_code_on_fail` |
| 6 | 无任何决策类规则适用 | `NO_RULE` | `POLICY_AMBIGUOUS` |

第 1 条在最前面，是为了让未签收订单（REFUND-UNDELIVERED-001，`order_delivered == false`）
稳定给出 `REQUIRE_HUMAN`：此时 `days_since_delivery` 为 `None`，标准退款规则会因窗口条件
不通过而给出 DENY，但"包裹还在路上"该走人工拦截，不该直接拒绝（契约 §2 订单 82919）。

`ALLOW` 不等于可以执行：金额是否超过 `max_auto_amount` 由决策层矩阵规则 10 判定，
写操作还需要用户确认（矩阵规则 12）。引擎只回答"政策上允不允许"。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from cs_agent.domain.enums import PolicyEffect, ReasonCode
from cs_agent.policy.facts import CONDITION_FIELDS, PolicyFacts
from cs_agent.policy.schema import Condition, PolicyRule, PolicySet

#: 参与资格判定的三种 effect。`informational` 不在其中。
DECISIONAL_EFFECTS: frozenset[PolicyEffect] = frozenset(
    {PolicyEffect.ALLOW_REFUND, PolicyEffect.DENY_REFUND, PolicyEffect.REQUIRE_HUMAN}
)


class PolicyOutcome(StrEnum):
    """策略引擎的五值结论。与用户可感知的 `DecisionOutcome` 是两回事，由决策层翻译。"""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    NO_RULE = "NO_RULE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """判定结果。`policy_id` / `policy_version` 必须回带（FR-402），供引用—执行一致性校验。"""

    outcome: PolicyOutcome
    reason_code: ReasonCode
    policy_id: str | None = None
    policy_version: int | None = None
    max_auto_amount: Decimal | None = None
    #: 排障用：本次求值中已通过的条件字段名，按 YAML 中的书写顺序。
    matched_conditions: tuple[str, ...] = ()
    #: 排障用：第一个不通过的条件字段名；通过时为 None。
    failed_condition: str | None = None
    #: 排障用：AMBIGUOUS 时同时命中的规则 id；其余情况为空。
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Match:
    """单条规则的求值中间结果。"""

    rule: PolicyRule
    passed: bool
    matched: tuple[str, ...] = field(default=())
    failed: str | None = None


class PolicyConfigError(ValueError):
    """策略 YAML 引用了 `PolicyFacts` 里没有的字段。属于配置错误，不是运行期分支。"""


def evaluate(facts: PolicyFacts, rules: PolicySet) -> PolicyVerdict:
    """对一组业务事实求值，返回唯一的 `PolicyVerdict`。纯函数。"""
    values = facts.as_condition_mapping()

    human_passed: list[_Match] = []
    deny_passed: list[_Match] = []
    allow_passed: list[_Match] = []
    allow_failed: list[_Match] = []

    for rule in rules.rules:
        if rule.effect not in DECISIONAL_EFFECTS:
            continue
        if not _scope_matches(rule, facts):
            continue
        match = _eval_rule(rule, values)
        if rule.effect is PolicyEffect.REQUIRE_HUMAN:
            if match.passed:
                human_passed.append(match)
        elif rule.effect is PolicyEffect.DENY_REFUND:
            # 契约 §8：deny_refund 未通过即"不适用"，直接跳过，不产生结论。
            if match.passed:
                deny_passed.append(match)
        else:  # ALLOW_REFUND
            (allow_passed if match.passed else allow_failed).append(match)

    if human_passed:
        return _verdict_on_pass(human_passed[0], PolicyOutcome.REQUIRE_HUMAN)
    if deny_passed:
        return _verdict_on_pass(deny_passed[0], PolicyOutcome.DENY)
    if len(allow_passed) > 1:
        return PolicyVerdict(
            outcome=PolicyOutcome.AMBIGUOUS,
            reason_code=ReasonCode.POLICY_AMBIGUOUS,
            candidates=tuple(m.rule.id for m in allow_passed),
        )
    if allow_passed:
        return _verdict_on_pass(allow_passed[0], PolicyOutcome.ALLOW)
    if allow_failed:
        return _verdict_on_fail(allow_failed[0])
    return PolicyVerdict(outcome=PolicyOutcome.NO_RULE, reason_code=ReasonCode.POLICY_AMBIGUOUS)


# --- applies_to ----------------------------------------------------------------


def _scope_matches(rule: PolicyRule, facts: PolicyFacts) -> bool:
    return _in_scope(rule.applies_to.item_category, facts.item_category) and _in_scope(
        rule.applies_to.user_tier, facts.user_tier
    )


def _in_scope(spec: str | list[str] | None, value: str) -> bool:
    if spec is None:
        return True
    if isinstance(spec, str):
        return value == spec
    return value in spec


# --- conditions ----------------------------------------------------------------


def _eval_rule(rule: PolicyRule, values: Mapping[str, object]) -> _Match:
    matched: list[str] = []
    for field_name, condition in rule.conditions.items():
        if field_name not in CONDITION_FIELDS:
            raise PolicyConfigError(
                f"{rule.id}: condition field {field_name!r} is not a PolicyFacts field"
            )
        if _condition_holds(condition, values[field_name]):
            matched.append(field_name)
        else:
            return _Match(rule=rule, passed=False, matched=tuple(matched), failed=field_name)
    return _Match(rule=rule, passed=True, matched=tuple(matched))


def _condition_holds(cond: Condition, value: object) -> bool:
    """schema 的 8 个操作符，同一字段上多个操作符取 AND。事实为 None 时一律不通过。"""
    if cond.eq is not None and not (value is not None and value == cond.eq):
        return False
    if cond.ne is not None and not (value is not None and value != cond.ne):
        return False
    if cond.lt is not None and not _numeric_cmp(value, cond.lt, "lt"):
        return False
    if cond.lte is not None and not _numeric_cmp(value, cond.lte, "lte"):
        return False
    if cond.gt is not None and not _numeric_cmp(value, cond.gt, "gt"):
        return False
    if cond.gte is not None and not _numeric_cmp(value, cond.gte, "gte"):
        return False
    if cond.in_ is not None and not (value is not None and value in cond.in_):
        return False
    if cond.not_in is not None and not (value is not None and value not in cond.not_in):
        return False
    return True


def _numeric_cmp(value: object, bound: int | float, op: str) -> bool:
    # bool 是 int 的子类，但"已签收 > 0"没有意义，按不通过处理。
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return False
    left: Decimal | float = value if isinstance(value, Decimal) else float(value)
    right: Decimal | float = Decimal(str(bound)) if isinstance(left, Decimal) else float(bound)
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "gt":
        return left > right
    return left >= right


# --- verdict 构造 ---------------------------------------------------------------


def _verdict_on_pass(match: _Match, outcome: PolicyOutcome) -> PolicyVerdict:
    rule = match.rule
    # schema 的 _effect_consistency 已保证决策类与 require_human 规则必有 pass 码。
    assert rule.reason_code_on_pass is not None
    return PolicyVerdict(
        outcome=outcome,
        reason_code=rule.reason_code_on_pass,
        policy_id=rule.id,
        policy_version=rule.version,
        max_auto_amount=rule.max_auto_amount,
        matched_conditions=match.matched,
    )


def _verdict_on_fail(match: _Match) -> PolicyVerdict:
    rule = match.rule
    assert match.failed is not None
    assert rule.reason_code_on_fail is not None
    reason = rule.fail_reason_codes.get(match.failed, rule.reason_code_on_fail)
    return PolicyVerdict(
        outcome=PolicyOutcome.DENY,
        reason_code=reason,
        policy_id=rule.id,
        policy_version=rule.version,
        max_auto_amount=rule.max_auto_amount,
        matched_conditions=match.matched,
        failed_condition=match.failed,
    )
