"""`agent.case_state` 的读写（PRD §10 第 ③ 层，FR-709）。

职责边界：只做序列化与 IO，**不含任何业务规则**。`CaseFacts` 的变更一律先经
`case_facts.py` 的纯函数算出来，再由这里整体落盘。

不变式 3 在这里有个具体体现：`save_narrative` 只动 `narrative_summary` 与
`summary_version`，SQL 里根本没有 `case_facts` 列；`save_facts` 反过来也不碰叙述。
压缩再怎么跑，都不可能顺手把 CaseFacts 压掉。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from cs_agent.db.base import get_engine
from cs_agent.memory.case_facts import CaseFacts

_LOAD = text(
    "SELECT case_facts, narrative_summary, summary_version "
    "FROM agent.case_state WHERE thread_id = :thread_id"
)

_SAVE_FACTS = text(
    """
    INSERT INTO agent.case_state (thread_id, case_facts, updated_at)
    VALUES (:thread_id, CAST(:case_facts AS jsonb), :updated_at)
    ON CONFLICT (thread_id) DO UPDATE SET
        case_facts = EXCLUDED.case_facts,
        updated_at = EXCLUDED.updated_at
    """
)

_SAVE_NARRATIVE = text(
    """
    INSERT INTO agent.case_state (thread_id, case_facts, narrative_summary,
                                  summary_version, updated_at)
    VALUES (:thread_id, '{}'::jsonb, :summary, 1, :updated_at)
    ON CONFLICT (thread_id) DO UPDATE SET
        narrative_summary = EXCLUDED.narrative_summary,
        summary_version   = agent.case_state.summary_version + 1,
        updated_at        = EXCLUDED.updated_at
    """
)


class CaseStateRepo:
    """一个 thread 一行。`engine` 可注入，测试与批处理各自带自己的连接。"""

    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine or get_engine()

    def load_facts(self, thread_id: UUID) -> CaseFacts:
        """没有行时返回空 `CaseFacts`，不抛异常——新会话的第一轮就是这种情况。"""
        with self.engine.connect() as conn:
            row = conn.execute(_LOAD, {"thread_id": thread_id}).mappings().first()
        return CaseFacts.from_json_dict(dict(row["case_facts"]) if row else None)

    def save_facts(self, thread_id: UUID, facts: CaseFacts, *, now: datetime | None = None) -> None:
        """整体覆盖写。`facts` 必须是纯函数算好的结果，这里不做任何合并。"""
        with self.engine.begin() as conn:
            conn.execute(
                _SAVE_FACTS,
                {
                    "thread_id": thread_id,
                    "case_facts": json.dumps(facts.to_json_dict(), ensure_ascii=False),
                    "updated_at": now or datetime.now(UTC),
                },
            )

    def load_narrative(self, thread_id: UUID) -> tuple[str | None, int]:
        with self.engine.connect() as conn:
            row = conn.execute(_LOAD, {"thread_id": thread_id}).mappings().first()
        return (None, 0) if row is None else (row["narrative_summary"], row["summary_version"])

    def save_narrative(self, thread_id: UUID, summary: str, *, now: datetime | None = None) -> None:
        """写叙述摘要并递增版本。SQL 里没有 `case_facts`：压缩碰不到事实（不变式 3）。"""
        with self.engine.begin() as conn:
            conn.execute(
                _SAVE_NARRATIVE,
                {
                    "thread_id": thread_id,
                    "summary": summary,
                    "updated_at": now or datetime.now(UTC),
                },
            )
