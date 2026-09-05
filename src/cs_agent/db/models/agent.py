"""schema `agent`：Agent 平台状态（PRD §7.3）。

LangGraph 的 `checkpoints` 等表由官方 checkpointer 自行建表，不在本模块声明。
本模块只描述表结构，不含任何业务规则；agent 表不与 biz 表建外键（禁止跨 schema 耦合）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
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


class Thread(Base):
    """会话。`id` 由服务端生成，与调用方身份绑定（FR-101）。"""

    __tablename__ = "threads"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    # 身份来自 AuthContext，不与 biz.users 建外键：agent 与 biz 是两套系统，不做跨 schema 耦合
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # 本阶段单租户，恒为 NULL；列先留着，避免多租户时再迁移一次（PRD §7.3）
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Message(Base):
    """原始消息。叙述压缩只作用于 `case_state.narrative_summary`，本表永不改写。"""

    __tablename__ = "messages"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey(f"{AGENT_SCHEMA}.threads.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseState(Base):
    """CaseFacts 物化副本（记忆不变式 2：只能被确定性代码写入，LLM 不得写）。

    `case_facts` 与 `pending_action` 永不参与压缩；被压缩的只有 `narrative_summary`。
    """

    __tablename__ = "case_state"
    __table_args__ = {"schema": AGENT_SCHEMA}

    thread_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey(f"{AGENT_SCHEMA}.threads.id"), primary_key=True
    )
    case_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    narrative_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentAction(Base):
    """写操作全生命周期：proposed → decided → executed。

    `idempotency_key` 唯一索引是防重复退款的**唯一**手段（PRD §7.4）：
    不允许用"先 SELECT 再 INSERT"替代，checkpoint 重放时靠它兜住重复副作用。
    """

    __tablename__ = "agent_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_agent_actions_idempotency_key"),
        {"schema": AGENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey(f"{AGENT_SCHEMA}.threads.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ReasonCode
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HumanReview(Base):
    """人工审批队列。`edited_params` 被修改后必须重算 `params_hash` 与幂等键（FR-605）。"""

    __tablename__ = "human_reviews"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    action_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{AGENT_SCHEMA}.agent_actions.id"), nullable=False, index=True
    )
    thread_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey(f"{AGENT_SCHEMA}.threads.id"), nullable=False, index=True
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)  # ReasonCode
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """**追加式**审计（PRD §7.4）：应用层不提供 UPDATE / DELETE 路径。

    `thread_id` / `action_id` 不建外键——审计写入不允许因被引用行的状态而失败。
    """

    __tablename__ = "audit_log"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)  # system / human / customer
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    thread_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    action_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ReasonCode
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class UserMemory(Base):
    """长期记忆，**非权威**（ADR-0009）。

    只影响语气、渠道偏好、上下文提示；不得作为归属、退款资格、权限、金额上限的输入。
    写入异步、带 `confidence` 与 `source_thread_id`、可通过 `deleted_at` 软删除。
    """

    __tablename__ = "user_memory"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    mem_value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    source_thread_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    ttl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryEmbedding(Base):
    """记忆向量。

    Phase 1 用 Text 占位（存 JSON 数组文本），Phase 2 换 pgvector 的 `vector(1536)` 并建 HNSW 索引。
    """

    __tablename__ = "memory_embeddings"
    __table_args__ = {"schema": AGENT_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    memory_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{AGENT_SCHEMA}.user_memory.id"), nullable=False, index=True
    )
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # TODO Phase 2: vector(1536)


class PolicyChunk(Base):
    """RAG 语料，由 `policies/*.yaml` 生成。

    唯一键 `(policy_id, policy_version, chunk_index)` 防版本混淆（PRD §7.4）。
    `embedding` 同 `MemoryEmbedding`：Phase 1 Text 占位，Phase 2 换 pgvector。
    """

    __tablename__ = "policy_chunks"
    __table_args__ = (
        UniqueConstraint(
            "policy_id", "policy_version", "chunk_index", name="uq_policy_chunks_policy_id"
        ),
        {"schema": AGENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    anchor: Mapped[str] = mapped_column(String(128), nullable=False)
    # 属性名避开 DeclarativeBase 保留的 `metadata`，列名仍按 PRD §7.3 叫 metadata
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # TODO Phase 2: vector(1536)


class RateLimitCounter(Base):
    """限流计数（FR-806）。P0 用 Postgres，P1 迁 Redis。

    `key` 形如 `user:101` / `ip:1.2.3.4` / `apikey:xxx`，三个维度共用本表。
    """

    __tablename__ = "rate_limit_counters"
    __table_args__ = {"schema": AGENT_SCHEMA}

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
