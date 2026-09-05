"""策略 YAML 的结构定义与加载（PRD §9.2）。

policies/*.yaml 是唯一事实来源：RAG chunk 由此生成（Phase 2），策略引擎由此求值（Phase 3）。
本模块只负责"读进来并校验形状"，不含任何判定逻辑。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cs_agent.domain.enums import PolicyDomain, PolicyEffect, ReasonCode

Scalar = int | float | str | bool

POLICY_ID_PATTERN = r"^[A-Z]+(-[A-Z0-9]+)+$"


class Condition(BaseModel):
    """单个事实字段上的比较条件。至少给一个操作符。

    事实字段名（如 days_since_delivery / item_condition / order_amount）由策略引擎
    从业务库实时计算后传入，不来自对话，不来自记忆（红线 3）。
    """

    model_config = ConfigDict(extra="forbid")

    eq: Scalar | None = None
    ne: Scalar | None = None
    lt: int | float | None = None
    lte: int | float | None = None
    gt: int | float | None = None
    gte: int | float | None = None
    in_: list[Scalar] | None = Field(default=None, alias="in")
    not_in: list[Scalar] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Condition:
        if not any(
            v is not None
            for v in (self.eq, self.ne, self.lt, self.lte, self.gt, self.gte, self.in_, self.not_in)
        ):
            raise ValueError("condition must specify at least one operator")
        if (self.eq is not None or self.ne is not None) and (
            self.in_ is not None or self.not_in is not None
        ):
            raise ValueError("eq/ne and in/not_in are mutually exclusive")
        return self


class AppliesTo(BaseModel):
    """规则适用范围。全部可选；都为空表示通用规则（仅 informational 允许）。"""

    model_config = ConfigDict(extra="forbid")

    item_category: str | list[str] | None = None
    user_tier: str | list[str] | None = None
    ticket_type: str | list[str] | None = None


class FaqEntry(BaseModel):
    """附着在规则下的问答对，用于生成长文 FAQ chunk（PRD §11 ①）。"""

    model_config = ConfigDict(extra="forbid")

    q: str
    a: str


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: Annotated[str, Field(pattern=POLICY_ID_PATTERN)]
    version: Annotated[int, Field(ge=1)]
    effective_date: date
    domain: PolicyDomain
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    conditions: dict[str, Condition] = Field(default_factory=dict)
    effect: PolicyEffect
    max_auto_amount: Decimal | None = None
    requires_approval_above: Decimal | None = None
    reason_code_on_pass: ReasonCode | None = None
    reason_code_on_fail: ReasonCode | None = None
    # 可选：按条件字段给出更具体的失败码（如 days_since_delivery → POLICY_VIOLATION_WINDOW）
    fail_reason_codes: dict[str, ReasonCode] = Field(default_factory=dict)
    anchor: Annotated[str, Field(pattern=r"^[a-z]+#[a-z0-9_-]+$")]
    title: str
    human_text: str
    faq: list[FaqEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _effect_consistency(self) -> PolicyRule:
        decisional = self.effect in (PolicyEffect.ALLOW_REFUND, PolicyEffect.DENY_REFUND)
        if decisional:
            if self.reason_code_on_pass is None or self.reason_code_on_fail is None:
                raise ValueError(f"{self.id}: decisional rule needs pass/fail reason codes")
            a = self.applies_to
            if not any((a.item_category, a.user_tier, a.ticket_type)):
                raise ValueError(f"{self.id}: decisional rule must declare applies_to")
        if self.effect is PolicyEffect.REQUIRE_HUMAN and self.reason_code_on_pass is None:
            raise ValueError(f"{self.id}: require_human rule needs reason_code_on_pass")
        if self.effect is PolicyEffect.INFORMATIONAL:
            forbidden = {
                "conditions": self.conditions,
                "max_auto_amount": self.max_auto_amount,
                "requires_approval_above": self.requires_approval_above,
                "reason_code_on_pass": self.reason_code_on_pass,
                "reason_code_on_fail": self.reason_code_on_fail,
                "fail_reason_codes": self.fail_reason_codes,
            }
            present = [k for k, v in forbidden.items() if v]
            if present:
                raise ValueError(f"{self.id}: informational rule must not set {present}")
        for key in self.fail_reason_codes:
            if key not in self.conditions:
                raise ValueError(f"{self.id}: fail_reason_codes key {key!r} not in conditions")
        if self.requires_approval_above is not None and self.max_auto_amount is not None:
            if self.requires_approval_above != self.max_auto_amount:
                raise ValueError(f"{self.id}: requires_approval_above must equal max_auto_amount")
        if self.anchor.split("#", 1)[0] != self.domain.value:
            raise ValueError(f"{self.id}: anchor prefix must equal domain {self.domain.value!r}")
        if not self.human_text.strip():
            raise ValueError(f"{self.id}: human_text is required")
        return self


class PolicySet(BaseModel):
    rules: list[PolicyRule]

    @model_validator(mode="after")
    def _unique_ids(self) -> PolicySet:
        seen: set[str] = set()
        for r in self.rules:
            if r.id in seen:
                raise ValueError(f"duplicate policy id {r.id}")
            seen.add(r.id)
        anchors = [r.anchor for r in self.rules]
        if len(anchors) != len(set(anchors)):
            raise ValueError("duplicate anchor")
        return self

    def by_id(self, policy_id: str) -> PolicyRule:
        for r in self.rules:
            if r.id == policy_id:
                return r
        raise KeyError(policy_id)


def load_policy_file(path: Path) -> list[PolicyRule]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top level must be a list of rules")
    return [PolicyRule.model_validate(item) for item in raw]


def load_policies(directory: Path) -> PolicySet:
    rules: list[PolicyRule] = []
    for path in sorted(directory.glob("*.yaml")):
        rules.extend(load_policy_file(path))
    return PolicySet(rules=rules)


Operator = Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in"]
