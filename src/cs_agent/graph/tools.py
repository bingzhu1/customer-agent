"""4 个只读工具（FR-201/202/203/204、FR-208/209）。

**工具签名里没有 `user_id` / `tenant_id`**（红线 1、ADR-0008）：身份在构造 `ToolBelt` 时
由 `AuthContext` 注入 `BizRepository`，每条查询自动带 scope。LLM 能提供的只有 `order_id`
这类业务标识，"查别人的订单"在语法上就写不出来。

他人数据与不存在的 id 一律返回 `None`（FR-804），调用方无从区分。

`search_policy` 现在走**真检索**：`rag.retriever.PolicyRetriever` 查 `agent.policy_chunks`
的 pgvector 向量，返回 `policy_id / policy_version / anchor / content / score`，
`score` 是真实相似度，可以直接喂给决策矩阵的 τ 门控（规则 10.5 / 13 / 14）。

语料由 `python -m cs_agent.rag.ingest` 从策略 YAML 生成；检索器与灌库必须用**同一个**
embedding provider，否则向量空间对不上，分数没有意义。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from cs_agent.eval.protocol import ToolCall
from cs_agent.graph.untrusted import wrap_untrusted
from cs_agent.policy.schema import PolicySet
from cs_agent.rag.retriever import PolicyRetriever, RetrievalResult
from cs_agent.repositories.biz import BizRepository

#: 单轮工具调用次数上限（FR-210）。超过即由决策层给 TOOL_BUDGET_EXCEEDED。
TOOL_BUDGET_PER_TURN = 3


@dataclass
class ToolBelt:
    """一次会话的工具集合。记录每次调用，供 runner 断言 `tools_called_*`。"""

    repo: BizRepository
    policies: PolicySet
    retriever: PolicyRetriever
    calls: list[ToolCall] = field(default_factory=list)
    #: 本轮最后一次检索的完整结果，供 `act` 节点取 max_score / band 喂给决策矩阵。
    last_retrieval: RetrievalResult | None = None

    # --- 只读工具 -------------------------------------------------------------

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        """订单主体 + 明细（FR-201）。非本人订单与不存在的订单同样返回 None。"""
        with self._record("get_order", {"order_id": order_id}):
            order = self.repo.get_order(order_id)
            if order is None:
                return None
            return {
                "order_id": order.id,
                "status": order.status,
                "total_amount": str(order.total_amount),
                "currency": order.currency,
                "placed_at": order.placed_at.isoformat(),
                "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
                # 买家留言是用户可写字段，按不可信内容包装（FR-209）
                "note": wrap_untrusted("order.note", order.note),
                "items": [
                    {
                        "sku": item.sku,
                        "name": item.name,
                        "category": item.category,
                        "qty": item.qty,
                        "unit_price": str(item.unit_price),
                        "item_condition": item.item_condition,
                    }
                    for item in self.repo.list_order_items(order_id)
                ],
            }

    def get_shipping(self, order_id: int) -> dict[str, Any] | None:
        """承运商 / 单号 / 状态 / 预计送达 / 最新轨迹（FR-202）。"""
        with self._record("get_shipping", {"order_id": order_id}):
            shipment = self.repo.get_shipping(order_id)
            if shipment is None:
                return None
            return {
                "order_id": shipment.order_id,
                "carrier": shipment.carrier,
                "tracking_no": shipment.tracking_no,
                "status": shipment.status,
                "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
                "estimated_delivery": (
                    shipment.estimated_delivery.isoformat() if shipment.estimated_delivery else None
                ),
                "last_event_at": (
                    shipment.last_event_at.isoformat() if shipment.last_event_at else None
                ),
                "last_event_desc": shipment.last_event_desc,
            }

    def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        """工单（FR-203）。归属校验同 `get_order`。"""
        with self._record("get_ticket", {"ticket_id": ticket_id}):
            ticket = self.repo.get_ticket(ticket_id)
            if ticket is None:
                return None
            return {
                "ticket_id": ticket.id,
                "type": ticket.type,
                "status": ticket.status,
                "priority": ticket.priority,
                "subject": ticket.subject,
                # 工单正文是用户原文，同样按不可信内容包装（FR-209）
                "body": wrap_untrusted("ticket.body", ticket.body),
                "created_at": ticket.created_at.isoformat(),
                "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
            }

    def search_policy(self, query: str, *, top_k: int | None = None) -> list[dict[str, Any]]:
        """政策检索（FR-204）。向量检索 top-k，返回带 policy_id / version / anchor 的 chunk。

        分数是真实相似度：`act` 会把 `max_score` 交给决策矩阵做 τ 门控，
        低置信时不允许走到"请用户确认"，无结果时不允许编造（规则 10.5 / 13 / 14）。
        """
        with self._record("search_policy", {"query": query}):
            result = self.retriever.search(query, top_k=top_k)
            self.last_retrieval = result
            return [
                {
                    "policy_id": c.policy_id,
                    "policy_version": c.policy_version,
                    "anchor": c.anchor,
                    "content": c.content,
                    "score": round(c.score, 4),
                }
                for c in result.chunks
            ]

    # --- 调用记录 -------------------------------------------------------------

    @property
    def budget_exceeded(self) -> bool:
        """FR-210：单轮超过 3 次工具调用即视为超预算。"""
        return len(self.calls) > TOOL_BUDGET_PER_TURN

    def reset_turn(self) -> None:
        self.calls = []

    def _record(self, name: str, arguments: dict[str, Any]) -> _Recorder:
        return _Recorder(self, name, arguments)


class _Recorder:
    """上下文管理器：记录一次工具调用的耗时与成败。"""

    def __init__(self, belt: ToolBelt, name: str, arguments: dict[str, Any]) -> None:
        self._belt = belt
        self._name = name
        self._arguments = arguments
        self._started = 0.0

    def __enter__(self) -> _Recorder:
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._belt.calls.append(
            ToolCall(
                name=self._name,
                arguments=self._arguments,
                ok=exc_type is None,
                error=None if exc is None else str(exc),
                latency_ms=round((time.perf_counter() - self._started) * 1000, 2),
            )
        )
