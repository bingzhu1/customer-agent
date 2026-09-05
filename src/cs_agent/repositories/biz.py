"""`biz` schema 的数据访问，**强制身份 scope**（FR-803、FR-804）。

每个查询都由本层拼上 `WHERE user_id = ctx.user_id`，调用方无法跳过：
方法签名里没有身份参数，身份只来自构造时注入的 `AuthContext`（ADR-0008）。

存在性保护（FR-804）：他人的数据与根本不存在的 id **返回完全相同的 `None`**，
调用方无从区分，避免通过枚举 id 探测数据是否存在。上层据此统一返回 `not_found`。

本层只查 `biz` 表，绝不与 `agent` 表出现在同一条 SQL 中（FR-807）。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from cs_agent.auth.context import AuthContext
from cs_agent.db.models.biz import Order, Shipment, Ticket


class BizRepository:
    """一次请求一个实例：`BizRepository(session, ctx)`。"""

    def __init__(self, session: Session, ctx: AuthContext) -> None:
        self._session = session
        self._ctx = ctx

    def get_order(self, order_id: int) -> Order | None:
        """订单主体。非本人订单与不存在的订单同样返回 `None`。"""
        stmt = select(Order).where(Order.id == order_id, Order.user_id == self._ctx.user_id)
        return self._session.scalars(stmt).one_or_none()

    def get_shipping(self, order_id: int) -> Shipment | None:
        """物流记录。`shipments` 自身没有 user_id，归属由 `orders` 决定，故必须 join 订单。"""
        stmt = (
            select(Shipment)
            .join(Order, Shipment.order_id == Order.id)
            .where(Order.id == order_id, Order.user_id == self._ctx.user_id)
        )
        return self._session.scalars(stmt).first()

    def get_ticket(self, ticket_id: int) -> Ticket | None:
        """工单。归属校验同 `get_order`。"""
        stmt = select(Ticket).where(Ticket.id == ticket_id, Ticket.user_id == self._ctx.user_id)
        return self._session.scalars(stmt).one_or_none()
