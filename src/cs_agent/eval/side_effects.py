"""副作用探针：runner 用它判定"这一轮到底有没有写库"，**不信任**被测方自述（protocol 约定）。

Phase 0 只看 biz.refunds / biz.tickets；agent.human_reviews 表在 Phase 4 之后才存在，存在时才计数。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import Engine, inspect, text

SideEffectKind = str  # "refund_created" | "ticket_created" | "human_review_created"


@dataclass(frozen=True)
class SideEffectSnapshot:
    refunds: int = 0
    tickets: int = 0
    human_reviews: int = 0

    def diff(self, later: SideEffectSnapshot) -> set[SideEffectKind]:
        """从 self 到 later 之间新增了哪些副作用。"""
        kinds: set[SideEffectKind] = set()
        if later.refunds > self.refunds:
            kinds.add("refund_created")
        if later.tickets > self.tickets:
            kinds.add("ticket_created")
        if later.human_reviews > self.human_reviews:
            kinds.add("human_review_created")
        return kinds


class SideEffectProbe(ABC):
    @abstractmethod
    def snapshot(self) -> SideEffectSnapshot: ...


class NullSideEffectProbe(SideEffectProbe):
    """无数据库时使用（单测 / 纯 LLM 基线）：永远报告无副作用。"""

    def snapshot(self) -> SideEffectSnapshot:
        return SideEffectSnapshot()


class DbSideEffectProbe(SideEffectProbe):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._has_human_reviews = inspect(engine).has_table("human_reviews", schema="agent")

    def snapshot(self) -> SideEffectSnapshot:
        with self._engine.connect() as conn:
            refunds = conn.execute(text("SELECT count(*) FROM biz.refunds")).scalar_one()
            tickets = conn.execute(text("SELECT count(*) FROM biz.tickets")).scalar_one()
            reviews = 0
            if self._has_human_reviews:
                reviews = conn.execute(
                    text("SELECT count(*) FROM agent.human_reviews")
                ).scalar_one()
        return SideEffectSnapshot(
            refunds=int(refunds), tickets=int(tickets), human_reviews=int(reviews)
        )
