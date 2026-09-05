"""向量列从 Text 占位换成 pgvector `vector(1536)`，policy_chunks 建 HNSW（cosine）索引

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-05

Phase 1 建表时 `policy_chunks.embedding` / `memory_embeddings.embedding` 是 Text 占位
（当时还没定 embedding provider）。Phase 2 落地 RAG，这里一次性换成真正的向量类型：

- 维度 1536 对应 `Settings.embedding_dimensions`（text-embedding-3-small）；改维度必须新写迁移，
  不允许只改配置——列类型与配置不一致会在插入时才炸。
- 只给 `policy_chunks` 建 HNSW：它是检索热路径（PRD §11 ③）；`memory_embeddings` 的检索
  在 Phase 5，数据量与访问模式都还没定，先不建索引，免得白付写入代价。
- 索引用 `vector_cosine_ops`：检索侧用 `<=>`（余弦距离），算子类必须与之匹配，否则走不到索引。
- 扩展 `vector` 由 docker init 建好，这里 `IF NOT EXISTS` 兜底（一次性测试库是裸库，没有它）。
  downgrade **不 DROP EXTENSION**：扩展是库级共享资源，别的对象可能在用。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT = "agent"
DIMENSIONS = 1536
HNSW_INDEX = "ix_agent_policy_chunks_embedding_hnsw"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Text → vector 用 I/O 转换：占位期列里要么是 NULL，要么已是 '[...]' 字面量
    for table in ("policy_chunks", "memory_embeddings"):
        op.execute(
            f"ALTER TABLE {AGENT}.{table} "
            f"ALTER COLUMN embedding TYPE vector({DIMENSIONS}) "
            f"USING embedding::vector({DIMENSIONS})"
        )
    op.execute(
        f"CREATE INDEX {HNSW_INDEX} ON {AGENT}.policy_chunks "
        f"USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {AGENT}.{HNSW_INDEX}")
    for table in ("policy_chunks", "memory_embeddings"):
        op.execute(
            f"ALTER TABLE {AGENT}.{table} ALTER COLUMN embedding TYPE text USING embedding::text"
        )
