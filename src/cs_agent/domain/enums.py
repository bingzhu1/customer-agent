"""受限枚举。来源：PRD §9.3（DecisionOutcome）、§9.5（reason_code）、§7.2（业务字段取值）。

这些枚举是评估断言与决策层的公共词表，新增值必须同步修改 PRD 并走评估回归。
"""

from enum import StrEnum


class DecisionOutcome(StrEnum):
    """6 值终态。`continue` / `retry` 是内部控制信号，不在此列。"""

    ANSWER = "ANSWER"
    REQUEST_INFO = "REQUEST_INFO"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    DENY = "DENY"
    DEGRADE = "DEGRADE"


class ReasonCode(StrEnum):
    OK = "OK"
    MISSING_ENTITY = "MISSING_ENTITY"
    POLICY_SATISFIED = "POLICY_SATISFIED"
    POLICY_VIOLATION_WINDOW = "POLICY_VIOLATION_WINDOW"
    POLICY_VIOLATION_CATEGORY = "POLICY_VIOLATION_CATEGORY"
    POLICY_VIOLATION_CONDITION = "POLICY_VIOLATION_CONDITION"
    POLICY_AMBIGUOUS = "POLICY_AMBIGUOUS"
    AMOUNT_ABOVE_AUTO_LIMIT = "AMOUNT_ABOVE_AUTO_LIMIT"
    LOW_CONFIDENCE_ON_DECISION = "LOW_CONFIDENCE_ON_DECISION"
    OWNERSHIP_MISMATCH = "OWNERSHIP_MISMATCH"
    AUTH_INSUFFICIENT = "AUTH_INSUFFICIENT"
    SUSPECTED_INJECTION = "SUSPECTED_INJECTION"
    RETRIEVAL_NO_RESULT = "RETRIEVAL_NO_RESULT"
    RETRIEVAL_LOW_CONFIDENCE = "RETRIEVAL_LOW_CONFIDENCE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    TOOL_FAILURE_REPEATED = "TOOL_FAILURE_REPEATED"
    TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
    CUSTOMER_ESCALATION_REQUEST = "CUSTOMER_ESCALATION_REQUEST"
    HIGH_NEGATIVE_SENTIMENT = "HIGH_NEGATIVE_SENTIMENT"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"


class ItemCategory(StrEnum):
    """`biz.order_items.category`，策略判定的关键维度（PRD §7.2）。"""

    STANDARD = "standard"
    FOOD = "food"
    CUSTOM = "custom"


class ItemCondition(StrEnum):
    UNUSED = "unused"
    UNOPENED = "unopened"
    USED = "used"
    DAMAGED = "damaged"


class UserTier(StrEnum):
    STANDARD = "standard"
    GOLD = "gold"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class ShipmentStatus(StrEnum):
    PENDING = "pending"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    EXCEPTION = "exception"
    LOST = "lost"


class PaymentStatus(StrEnum):
    PAID = "paid"
    REFUNDED = "refunded"
    PARTIAL_REFUND = "partial_refund"
    FAILED = "failed"


class TicketType(StrEnum):
    COMPLAINT = "complaint"
    INQUIRY = "inquiry"
    REFUND = "refund"
    SHIPPING = "shipping"
    WARRANTY = "warranty"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PolicyEffect(StrEnum):
    """策略规则的效果。informational 规则只用于 RAG 回答，不参与资格判定。"""

    ALLOW_REFUND = "allow_refund"
    DENY_REFUND = "deny_refund"
    INFORMATIONAL = "informational"
    REQUIRE_HUMAN = "require_human"


class PolicyDomain(StrEnum):
    REFUND = "refund"
    SHIPPING = "shipping"
    WARRANTY = "warranty"
    MEMBERSHIP = "membership"
    COMPLAINT = "complaint"


class GoldenCategory(StrEnum):
    """PRD §12.2 的七个用例类别。"""

    POLICY = "policy"
    ORDER = "order"
    SECURITY = "security"
    ESCALATION = "escalation"
    MEMORY = "memory"
    RAG = "rag"
    IDEMPOTENCY = "idempotency"
