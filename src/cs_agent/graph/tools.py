"""4 个只读工具（FR-201/202/203/204、FR-208/209）。

**工具签名里没有 `user_id` / `tenant_id`**（红线 1、ADR-0008）：身份在构造 `ToolBelt` 时
由 `AuthContext` 注入 `BizRepository`，每条查询自动带 scope。LLM 能提供的只有 `order_id`
这类业务标识，"查别人的订单"在语法上就写不出来。

他人数据与不存在的 id 一律返回 `None`（FR-804），调用方无从区分。

`search_policy` 是**占位实现，不是真 RAG**：用策略 YAML 的 `title / human_text / faq`
做关键词打分，返回 `policy_id / policy_version / anchor`。Phase 2 换成 pgvector 检索后，
返回结构不变，只换打分来源。因此这里的 `score` 不是相似度，别拿它当 τ 门控的输入。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from cs_agent.eval.protocol import ToolCall
from cs_agent.graph.untrusted import wrap_untrusted
from cs_agent.policy.schema import PolicySet
from cs_agent.repositories.biz import BizRepository

#: 单轮工具调用次数上限（FR-210）。超过即由决策层给 TOOL_BUDGET_EXCEEDED。
TOOL_BUDGET_PER_TURN = 3

_CJK = r"一-鿿"
_TOKEN_RE = re.compile(rf"[a-zA-Z0-9]+|[{_CJK}]")


def _keywords(text: str) -> list[str]:
    """英文按词、中文按二元组切分。朴素但确定，够占位用。"""
    chars = _TOKEN_RE.findall(text)
    words = [c for c in chars if not re.match(rf"[{_CJK}]", c)]
    cjk = [c for c in chars if re.match(rf"[{_CJK}]", c)]
    bigrams = ["".join(pair) for pair in zip(cjk, cjk[1:], strict=False)]
    return [w.lower() for w in words] + bigrams


@dataclass
class ToolBelt:
    """一次会话的工具集合。记录每次调用，供 runner 断言 `tools_called_*`。"""

    repo: BizRepository
    policies: PolicySet
    calls: list[ToolCall] = field(default_factory=list)

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

    def search_policy(self, query: str, *, top_k: int = 3) -> list[dict[str, Any]]:
        """政策检索（FR-204 的占位实现）。返回带 policy_id / version / anchor 的条目。"""
        with self._record("search_policy", {"query": query}):
            wanted = set(_keywords(query))
            if not wanted:
                return []
            scored: list[tuple[float, dict[str, Any]]] = []
            for rule in self.policies.rules:
                haystack = " ".join(
                    [rule.title, rule.human_text]
                    + [f"{f.q} {f.a}" for f in rule.faq]
                    + [rule.domain.value]
                )
                hits = wanted & set(_keywords(haystack))
                if not hits:
                    continue
                scored.append(
                    (
                        len(hits) / len(wanted),
                        {
                            "policy_id": rule.id,
                            "policy_version": rule.version,
                            "anchor": rule.anchor,
                            "title": rule.title,
                            "content": rule.human_text,
                            "score": round(len(hits) / len(wanted), 4),
                        },
                    )
                )
            scored.sort(key=lambda pair: (-pair[0], pair[1]["policy_id"]))
            return [item for _, item in scored[:top_k]]

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
