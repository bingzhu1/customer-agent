"""策略引擎的唯一输入：`PolicyFacts`（PRD §9.2 / FR-401 / FR-403，ADR-0009）。

**红线 3：本结构的每一个字段都必须由确定性代码从业务库（`biz` schema）实时查出后填充。**

- 不得从对话内容、LLM 输出、`ActionProposal` 参数中填充；
- 不得从 `user_memory` / `case_state` / 任何长期记忆中填充；
- 特别地，`user_tier` 只能来自 `biz.users.tier`，不能来自记忆里的"该用户是 VIP"；
  `order_amount` 只能来自 `biz.orders.total_amount`，不能复用用户在对话中说的金额。

结构上保证越权不可表达：本结构里根本没有记忆类字段，`evaluate()` 也不接受记忆参数，
因此"记忆放宽退款额度"这类投毒攻击在类型层面就无法写出来（ADR-0009 强制手段第 3 条）。

`days_since_delivery` 由 `(EVAL_NOW - delivered_at).days` 计算（契约 §0，时钟可注入）；
未签收订单为 `None`，此时任何数值条件一律判定为不通过，由 REFUND-UNDELIVERED-001 兜底转人工。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from cs_agent.domain.enums import ItemCategory, ItemCondition, UserTier

#: 允许出现在策略 YAML `conditions` 中的事实字段名。
#: `order_id` 刻意不在其中——策略是规则，不能针对单个订单开后门。
CONDITION_FIELDS: frozenset[str] = frozenset(
    {
        "user_tier",
        "item_category",
        "item_condition",
        "order_amount",
        "order_delivered",
        "days_since_delivery",
        "prior_refund_exists",
    }
)


@dataclass(frozen=True, slots=True)
class PolicyFacts:
    """一次退款资格判定所需的全部业务事实。冻结不可变，便于审计与复现。"""

    order_id: int
    user_tier: UserTier
    item_category: ItemCategory
    item_condition: ItemCondition
    order_amount: Decimal
    order_delivered: bool
    days_since_delivery: int | None
    prior_refund_exists: bool

    def as_condition_mapping(self) -> Mapping[str, object]:
        """供条件求值使用的字段视图。键集合恒等于 `CONDITION_FIELDS`。"""
        return {
            "user_tier": self.user_tier,
            "item_category": self.item_category,
            "item_condition": self.item_condition,
            "order_amount": self.order_amount,
            "order_delivered": self.order_delivered,
            "days_since_delivery": self.days_since_delivery,
            "prior_refund_exists": self.prior_refund_exists,
        }
