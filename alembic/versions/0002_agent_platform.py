"""Agent 平台表：threads / messages / case_state / agent_actions / human_reviews /
audit_log / user_memory / memory_embeddings / policy_chunks / rate_limit_counters

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05

表结构对应 PRD §7.3，关键约束对应 §7.4：
- `agent_actions.idempotency_key` 唯一索引（防重复退款，不允许用"先查再写"替代）
- `policy_chunks (policy_id, policy_version, chunk_index)` 唯一（防版本混淆）
向量列本阶段用 Text 占位，Phase 2 换 pgvector `vector(1536)` 并建 HNSW 索引。
agent 表不向 biz 建外键：两套系统的边界靠应用层维护，不做跨 schema 耦合。
LangGraph 的 checkpoints 等表由官方 checkpointer 自行建表，不在本迁移内。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT = "agent"


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # ---- agent.threads：会话，id 服务端生成，user_id 来自 AuthContext ----
    op.create_table(
        "threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # 单租户阶段恒为 NULL，列先留着，避免多租户时再迁移一次
        sa.Column("tenant_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        schema=AGENT,
    )
    op.create_index("ix_agent_threads_user_id", "threads", ["user_id"], schema=AGENT)

    # ---- agent.messages：原始消息，永不改写 ----
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"], [f"{AGENT}.threads.id"], name="fk_messages_thread_id_threads"
        ),
        schema=AGENT,
    )
    op.create_index("ix_agent_messages_thread_id", "messages", ["thread_id"], schema=AGENT)

    # ---- agent.case_state：CaseFacts 物化副本，只能被确定性代码写入 ----
    op.create_table(
        "case_state",
        sa.Column("thread_id", sa.Uuid(), primary_key=True),
        sa.Column("case_facts", _jsonb(), nullable=False),
        sa.Column("narrative_summary", sa.Text(), nullable=True),
        sa.Column("summary_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"], [f"{AGENT}.threads.id"], name="fk_case_state_thread_id_threads"
        ),
        schema=AGENT,
    )

    # ---- agent.agent_actions：写操作全生命周期，UNIQUE(idempotency_key) 是防重复的唯一手段 ----
    op.create_table(
        "agent_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("params", _jsonb(), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("result", _jsonb(), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["thread_id"], [f"{AGENT}.threads.id"], name="fk_agent_actions_thread_id_threads"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_actions_idempotency_key"),
        schema=AGENT,
    )
    op.create_index(
        "ix_agent_agent_actions_thread_id", "agent_actions", ["thread_id"], schema=AGENT
    )
    op.create_index("ix_agent_agent_actions_user_id", "agent_actions", ["user_id"], schema=AGENT)

    # ---- agent.human_reviews：人工审批队列 ----
    op.create_table(
        "human_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("action_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assigned_to", sa.String(128), nullable=True),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("edited_params", _jsonb(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id"],
            [f"{AGENT}.agent_actions.id"],
            name="fk_human_reviews_action_id_agent_actions",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], [f"{AGENT}.threads.id"], name="fk_human_reviews_thread_id_threads"
        ),
        schema=AGENT,
    )
    op.create_index(
        "ix_agent_human_reviews_action_id", "human_reviews", ["action_id"], schema=AGENT
    )
    op.create_index(
        "ix_agent_human_reviews_thread_id", "human_reviews", ["thread_id"], schema=AGENT
    )

    # ---- agent.audit_log：追加式，无 UPDATE / DELETE 路径；不建外键以免审计写入失败 ----
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("action_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("payload", _jsonb(), nullable=False),
        schema=AGENT,
    )
    op.create_index("ix_agent_audit_log_ts", "audit_log", ["ts"], schema=AGENT)
    op.create_index("ix_agent_audit_log_thread_id", "audit_log", ["thread_id"], schema=AGENT)

    # ---- agent.user_memory：长期记忆，非权威，软删除 ----
    op.create_table(
        "user_memory",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mem_key", sa.String(128), nullable=False),
        sa.Column("mem_value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("source_thread_id", sa.Uuid(), nullable=True),
        sa.Column("ttl_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema=AGENT,
    )
    op.create_index("ix_agent_user_memory_user_id", "user_memory", ["user_id"], schema=AGENT)

    # ---- agent.memory_embeddings：向量 Text 占位，Phase 2 换 pgvector ----
    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("memory_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            [f"{AGENT}.user_memory.id"],
            name="fk_memory_embeddings_memory_id_user_memory",
        ),
        schema=AGENT,
    )
    op.create_index(
        "ix_agent_memory_embeddings_memory_id", "memory_embeddings", ["memory_id"], schema=AGENT
    )

    # ---- agent.policy_chunks：RAG 语料，由 YAML 生成；唯一键防版本混淆 ----
    op.create_table(
        "policy_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("policy_id", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("anchor", sa.String(128), nullable=False),
        sa.Column("metadata", _jsonb(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "policy_id", "policy_version", "chunk_index", name="uq_policy_chunks_policy_id"
        ),
        schema=AGENT,
    )

    # ---- agent.rate_limit_counters：限流计数，P1 迁 Redis ----
    op.create_table(
        "rate_limit_counters",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        schema=AGENT,
    )


def downgrade() -> None:
    # 按外键依赖逆序删除
    op.drop_table("rate_limit_counters", schema=AGENT)
    op.drop_table("policy_chunks", schema=AGENT)
    op.drop_index(
        "ix_agent_memory_embeddings_memory_id", table_name="memory_embeddings", schema=AGENT
    )
    op.drop_table("memory_embeddings", schema=AGENT)
    op.drop_index("ix_agent_user_memory_user_id", table_name="user_memory", schema=AGENT)
    op.drop_table("user_memory", schema=AGENT)
    op.drop_index("ix_agent_audit_log_thread_id", table_name="audit_log", schema=AGENT)
    op.drop_index("ix_agent_audit_log_ts", table_name="audit_log", schema=AGENT)
    op.drop_table("audit_log", schema=AGENT)
    op.drop_index("ix_agent_human_reviews_thread_id", table_name="human_reviews", schema=AGENT)
    op.drop_index("ix_agent_human_reviews_action_id", table_name="human_reviews", schema=AGENT)
    op.drop_table("human_reviews", schema=AGENT)
    op.drop_index("ix_agent_agent_actions_user_id", table_name="agent_actions", schema=AGENT)
    op.drop_index("ix_agent_agent_actions_thread_id", table_name="agent_actions", schema=AGENT)
    op.drop_table("agent_actions", schema=AGENT)
    op.drop_table("case_state", schema=AGENT)
    op.drop_index("ix_agent_messages_thread_id", table_name="messages", schema=AGENT)
    op.drop_table("messages", schema=AGENT)
    op.drop_index("ix_agent_threads_user_id", table_name="threads", schema=AGENT)
    op.drop_table("threads", schema=AGENT)
