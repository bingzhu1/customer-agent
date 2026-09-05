"""`agent` schema 的数据访问，同样**强制身份 scope**（FR-803/804、FR-104）。

`threads` 与 `messages` 按 `threads.user_id` 收口：他人的会话与不存在的会话
一律返回 `None`，路由层统一翻成 404，不区分两者（PRD §8.4 的 404 设计意图）。

本层只查 `agent` 表，绝不与 `biz` 表出现在同一条 SQL 中（FR-807）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from cs_agent.auth.context import AuthContext
from cs_agent.db.models.agent import CaseState, Message, Thread

THREAD_STATUS_OPEN = "open"


class ThreadRepository:
    """一次请求一个实例：`ThreadRepository(session, ctx)`。"""

    def __init__(self, session: Session, ctx: AuthContext) -> None:
        self._session = session
        self._ctx = ctx

    def create_thread(self, *, now: datetime | None = None) -> Thread:
        """新建会话。`user_id` 来自 `AuthContext`，调用方无从指定（红线 1）。"""
        moment = now or datetime.now(UTC)
        thread = Thread(
            id=uuid4(),
            user_id=self._ctx.user_id,
            status=THREAD_STATUS_OPEN,
            created_at=moment,
            last_active_at=moment,
        )
        self._session.add(thread)
        self._session.flush()
        return thread

    def get_thread(self, thread_id: UUID) -> Thread | None:
        """他人会话与不存在的会话同样返回 `None`。"""
        stmt = select(Thread).where(Thread.id == thread_id, Thread.user_id == self._ctx.user_id)
        return self._session.scalars(stmt).one_or_none()

    def list_messages(self, thread_id: UUID) -> list[Message]:
        """按时间顺序取消息。会话不属于本人时返回空列表。"""
        stmt = (
            select(Message)
            .join(Thread, Message.thread_id == Thread.id)
            .where(Thread.id == thread_id, Thread.user_id == self._ctx.user_id)
            .order_by(Message.id)
        )
        return list(self._session.scalars(stmt))

    def add_message(
        self,
        thread_id: UUID,
        *,
        role: str,
        content: str,
        token_count: int | None = None,
        now: datetime | None = None,
    ) -> Message:
        """追加一条消息并顺带刷新 `last_active_at`。调用方须先确认会话归属。"""
        moment = now or datetime.now(UTC)
        message = Message(
            thread_id=thread_id,
            role=role,
            content=content,
            token_count=token_count,
            created_at=moment,
        )
        self._session.add(message)
        thread = self.get_thread(thread_id)
        if thread is not None:
            thread.last_active_at = moment
        self._session.flush()
        return message

    def get_case_state(self, thread_id: UUID) -> CaseState | None:
        """CaseFacts 物化副本。同样按会话归属收口。"""
        stmt = (
            select(CaseState)
            .join(Thread, CaseState.thread_id == Thread.id)
            .where(Thread.id == thread_id, Thread.user_id == self._ctx.user_id)
        )
        return self._session.scalars(stmt).one_or_none()
