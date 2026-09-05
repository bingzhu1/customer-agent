"""schema `agent`：Agent 平台状态（PRD §7.3）。

Phase 0 只建评估相关两张表；threads / messages / case_state / agent_actions 等随后续 Phase 加入。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cs_agent.db.base import Base

AGENT_SCHEMA = "agent"


class EvalRun(Base):
    """一次评估批次。"""

    __tablename__ = "eval_runs"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class EvalResult(Base):
    """单条 golden 用例的评估结果。"""

    __tablename__ = "eval_results"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{AGENT_SCHEMA}.eval_runs.id"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)  # DecisionOutcome
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ReasonCode
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
