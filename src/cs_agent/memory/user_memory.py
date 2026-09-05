"""`agent.user_memory` + `agent.memory_embeddings`（PRD §10 第 ④ 层，FR-705/706/710）。

**非权威层（ADR-0009）**：这里存的东西只能影响语气、渠道偏好、上下文提示。
本模块不 import `cs_agent.policy` / `cs_agent.decision`，也不提供任何"判定"接口——
拿到 `MemoryRecord` 的调用方能做的只有拼提示词（见 `memory/inject.py`）。

四条设计要点：

- **版本**：同 `(user_id, mem_key)` 再次写入 = 覆盖值并 `version + 1`（FR-710）；
- **软删除**：`delete` 只写 `deleted_at`，检索一律过滤（FR-706）。
  `upsert` **不会**把软删的条目复活——删除是用户意愿，不能被下一次抽取悄悄推翻；
- **TTL**：默认 180 天（§10.4）。过期条目不参与检索但保留在表里，便于审计；
  续期是显式的 `renew`，不做成 `search` 的副作用——检索在热路径上，读接口不该写库；
- **向量**：用 `rag.embeddings` 的 provider，与 RAG 同一套 pgvector 设施。

向量列在 0003 迁移后是 `vector(1536)`，这里全部走 `CAST(:v AS vector)`，
不引入 pgvector 的 Python 包。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from cs_agent.db.base import get_engine
from cs_agent.rag.embeddings import EmbeddingProvider, format_vector

#: PRD §10.4：默认 TTL 180 天。
DEFAULT_TTL_DAYS = 180


class MemoryRecord(BaseModel):
    """一条长期记忆。`score` 只在检索结果里有值。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    user_id: int
    mem_key: str
    mem_value: str
    confidence: float
    source_thread_id: UUID | None = None
    ttl_at: datetime | None = None
    version: int
    score: float | None = Field(default=None, description="余弦相似度，仅检索结果携带")


_SELECT_COLUMNS = (
    "m.id, m.user_id, m.mem_key, m.mem_value, m.confidence, m.source_thread_id, m.ttl_at, m.version"
)

_FIND = text(
    f"SELECT {_SELECT_COLUMNS} FROM agent.user_memory m "
    "WHERE m.user_id = :user_id AND m.mem_key = :mem_key"
)

_INSERT = text(
    """
    INSERT INTO agent.user_memory
        (user_id, mem_key, mem_value, confidence, source_thread_id, ttl_at,
         version, created_at, updated_at)
    VALUES (:user_id, :mem_key, :mem_value, :confidence, :source_thread_id, :ttl_at,
            1, :now, :now)
    RETURNING id, version
    """
)

_UPDATE = text(
    """
    UPDATE agent.user_memory
    SET mem_value = :mem_value,
        confidence = :confidence,
        source_thread_id = :source_thread_id,
        ttl_at = :ttl_at,
        version = version + 1,
        updated_at = :now
    WHERE id = :id
    RETURNING id, version
    """
)

_DELETE = text(
    """
    UPDATE agent.user_memory SET deleted_at = :now, updated_at = :now
    WHERE user_id = :user_id AND mem_key = :mem_key AND deleted_at IS NULL
    """
)

_RENEW = text(
    """
    UPDATE agent.user_memory SET ttl_at = :ttl_at, updated_at = :now
    WHERE user_id = :user_id AND mem_key = ANY(:mem_keys) AND deleted_at IS NULL
    """
)

_UPSERT_EMBEDDING = text(
    """
    INSERT INTO agent.memory_embeddings (memory_id, embedding)
    VALUES (:memory_id, CAST(:embedding AS vector))
    """
)

_DROP_EMBEDDINGS = text("DELETE FROM agent.memory_embeddings WHERE memory_id = :memory_id")

#: 检索：向量相似 + 未软删 + 未过期 + 限定本人。
#: `user_id` 条件是硬隔离——记忆是按人存的，跨用户检索等于数据泄露。
_SEARCH = text(
    f"""
    SELECT {_SELECT_COLUMNS},
           1 - (e.embedding <=> CAST(:query AS vector)) AS score
    FROM agent.user_memory m
    JOIN agent.memory_embeddings e ON e.memory_id = m.id
    WHERE m.user_id = :user_id
      AND m.deleted_at IS NULL
      AND (m.ttl_at IS NULL OR m.ttl_at > :now)
      AND e.embedding IS NOT NULL
    ORDER BY e.embedding <=> CAST(:query AS vector)
    LIMIT :top_k
    """
)

#: 固定注入的 key：这类偏好对**每一轮**都有效，与当前问句像不像无关。
#: 例如"希望用英文沟通"跟"帮我催单"毫无相似度，纯向量 top_k 会在记忆变多后把它挤掉，
#: 结果就是用户明明说过要英文、客服却一直用中文。语言偏好只影响回复语言，不碰任何判定（红线 3）。
ALWAYS_INJECT_KEYS: tuple[str, ...] = ("language_preference",)

_PINNED = text(
    f"""
    SELECT {_SELECT_COLUMNS},
           1 - (e.embedding <=> CAST(:query AS vector)) AS score
    FROM agent.user_memory m
    JOIN agent.memory_embeddings e ON e.memory_id = m.id
    WHERE m.user_id = :user_id
      AND m.mem_key = ANY(:keys)
      AND m.deleted_at IS NULL
      AND (m.ttl_at IS NULL OR m.ttl_at > :now)
      AND e.embedding IS NOT NULL
    ORDER BY e.embedding <=> CAST(:query AS vector)
    """
)


def _record(row: Any, *, score: float | None = None) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        user_id=row["user_id"],
        mem_key=row["mem_key"],
        mem_value=row["mem_value"],
        confidence=float(row["confidence"]),
        source_thread_id=row["source_thread_id"],
        ttl_at=row["ttl_at"],
        version=row["version"],
        score=score,
    )


class UserMemoryRepo:
    """长期记忆的读写。`provider` 必须与写入时用的一致，否则向量空间对不上。"""

    def __init__(self, provider: EmbeddingProvider, *, engine: Engine | None = None) -> None:
        self._provider = provider
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine or get_engine()

    def upsert(
        self,
        user_id: int,
        mem_key: str,
        mem_value: str,
        *,
        confidence: float,
        source_thread_id: UUID | None = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        now: datetime | None = None,
    ) -> MemoryRecord:
        """写入或覆盖一条记忆；同 key 覆盖并 `version + 1`（FR-710）。

        向量与记忆在同一个事务里更新：不允许出现"值变了但向量还是旧的"的中间态。
        """
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence 必须在 [0,1]，收到 {confidence}")
        moment = now or datetime.now(UTC)
        ttl_at = moment + timedelta(days=ttl_days) if ttl_days > 0 else None
        vector = self._provider.embed_query(mem_value)

        with self.engine.begin() as conn:
            existing = (
                conn.execute(_FIND, {"user_id": user_id, "mem_key": mem_key}).mappings().first()
            )
            params: dict[str, Any] = {
                "mem_value": mem_value,
                "confidence": Decimal(str(round(confidence, 3))),
                "source_thread_id": source_thread_id,
                "ttl_at": ttl_at,
                "now": moment,
            }
            if existing is None:
                written = (
                    conn.execute(_INSERT, {**params, "user_id": user_id, "mem_key": mem_key})
                    .mappings()
                    .one()
                )
            else:
                written = conn.execute(_UPDATE, {**params, "id": existing["id"]}).mappings().one()
            self._write_embedding(conn, int(written["id"]), vector)
            row = conn.execute(_FIND, {"user_id": user_id, "mem_key": mem_key}).mappings().one()
            return _record(row)

    def _write_embedding(self, conn: Connection, memory_id: int, vector: list[float]) -> None:
        if len(vector) != self._provider.dimensions:
            raise RuntimeError(f"memory {memory_id}: 向量维度不符")
        conn.execute(_DROP_EMBEDDINGS, {"memory_id": memory_id})
        conn.execute(
            _UPSERT_EMBEDDING, {"memory_id": memory_id, "embedding": format_vector(vector)}
        )

    def search(
        self,
        user_id: int,
        query: str,
        top_k: int = 5,
        *,
        now: datetime | None = None,
        pinned_keys: Sequence[str] = ALWAYS_INJECT_KEYS,
    ) -> list[MemoryRecord]:
        """向量检索本人的、未软删、未过期的记忆。返回顺序即相似度降序。

        `pinned_keys` 里的 key 若存在则**一定**在结果里：没进 top_k 的追加在末尾
        （它们的分数必然不高于第 k 条，所以整体仍是降序）。传空序列可关掉固定注入。

        **返回值只能用于语气与上下文提示**，不得进入任何资格 / 金额 / 权限判断（红线 3）。
        """
        vector = self._provider.embed_query(query)
        params = {
            "user_id": user_id,
            "query": format_vector(vector),
            "now": now or datetime.now(UTC),
        }
        with self.engine.connect() as conn:
            rows = conn.execute(_SEARCH, {**params, "top_k": top_k}).mappings().all()
            pinned_rows = (
                conn.execute(_PINNED, {**params, "keys": list(pinned_keys)}).mappings().all()
                if pinned_keys
                else []
            )
        out = [_record(r, score=float(r["score"])) for r in rows]
        seen = {m.id for m in out}
        out += [_record(r, score=float(r["score"])) for r in pinned_rows if r["id"] not in seen]
        return out

    def get(self, user_id: int, mem_key: str) -> MemoryRecord | None:
        """按 key 精确取，**包含**已软删与已过期的条目——供审计与调试，不供检索。"""
        with self.engine.connect() as conn:
            row = conn.execute(_FIND, {"user_id": user_id, "mem_key": mem_key}).mappings().first()
        return None if row is None else _record(row)

    def delete(self, user_id: int, mem_key: str, *, now: datetime | None = None) -> bool:
        """软删除（FR-706）。返回是否真的删到了；重复删除返回 False。"""
        with self.engine.begin() as conn:
            result = conn.execute(
                _DELETE, {"user_id": user_id, "mem_key": mem_key, "now": now or datetime.now(UTC)}
            )
        return result.rowcount > 0

    def renew(
        self,
        user_id: int,
        mem_keys: list[str],
        *,
        ttl_days: int = DEFAULT_TTL_DAYS,
        now: datetime | None = None,
    ) -> int:
        """命中续期（§10.4）。显式调用，不做成 `search` 的副作用。返回续期条数。"""
        if not mem_keys:
            return 0
        moment = now or datetime.now(UTC)
        with self.engine.begin() as conn:
            result = conn.execute(
                _RENEW,
                {
                    "user_id": user_id,
                    "mem_keys": mem_keys,
                    "ttl_at": moment + timedelta(days=ttl_days),
                    "now": moment,
                },
            )
        return int(result.rowcount)
