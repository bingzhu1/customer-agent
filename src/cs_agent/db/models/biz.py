"""schema `biz`：模拟企业业务系统，权威事实（PRD §7.2）。

枚举列以字符串存储，取值见 `cs_agent.domain.enums`；金额 Numeric(12,2)，时间带时区。
本模块只描述表结构，不含任何业务规则。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cs_agent.db.base import Base

BIZ_SCHEMA = "biz"


class User(Base):
    """客户主体。"""

    __tablename__ = "users"
    __table_args__ = {"schema": BIZ_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)  # UserTier
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Order(Base):
    """订单主体。`note` 为买家留言，间接注入用例依赖该列。"""

    __tablename__ = "orders"
    __table_args__ = {"schema": BIZ_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # OrderStatus
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderItem(Base):
    """订单明细。`category` 与 `item_condition` 是策略判定的关键维度。"""

    __tablename__ = "order_items"
    __table_args__ = {"schema": BIZ_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.orders.id"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # ItemCategory
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    item_condition: Mapped[str] = mapped_column(String(32), nullable=False)  # ItemCondition


class Shipment(Base):
    """物流记录。"""

    __tablename__ = "shipments"
    __table_args__ = {"schema": BIZ_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.orders.id"), nullable=False, index=True
    )
    carrier: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_no: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # ShipmentStatus
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_delivery: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_desc: Mapped[str | None] = mapped_column(Text, nullable=True)


class Payment(Base):
    """支付记录。"""

    __tablename__ = "payments"
    __table_args__ = {"schema": BIZ_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.orders.id"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # PaymentStatus
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Ticket(Base):
    """工单。`body` 为用户原文，属不可信内容。"""

    __tablename__ = "tickets"
    __table_args__ = {"schema": BIZ_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.users.id"), nullable=False, index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.orders.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # TicketType
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # TicketStatus
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Refund(Base):
    """退款结果，由 `RefundService(SIMULATED)` 写入。`simulated` 标记本阶段不接真实支付通道。"""

    __tablename__ = "refunds"
    __table_args__ = {"schema": BIZ_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.orders.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{BIZ_SCHEMA}.users.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # RefundStatus
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
