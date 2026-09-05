"""pgvector 检索 + 阈值门控（FR-303 / FR-307、PRD §11 ③，ADR-0007）。

`search_policy` 工具（Phase 1 拥有）调用本模块，把 `RetrievalResult` 转成工具返回值；
本模块**不返回任何决策**，只给分数与分带，决策由决策层按矩阵规则 13 / 14 处理：

```
max_score < τ_low            → band=no_result  → REQUIRE_HUMAN / RETRIEVAL_NO_RESULT
τ_low ≤ max_score < τ_high   → band=low        → ANSWER + confidence=low（规则 14）
max_score ≥ τ_high           → band=normal     → 正常回答
```

τ 是配置项（`Settings.rag_tau_low` / `rag_tau_high`），不硬编码；构造时也可显式覆盖，
标定脚本就是靠这个扫不同取值的。

分数 = 余弦相似度 = `1 - (embedding <=> query)`，与 HNSW 的 `vector_cosine_ops` 对齐。
band=no_result 时 chunks 仍原样返回：调用方要把它们写进 trace / `TurnResult.retrieved`
以便排障，但**不得据此作答**。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from cs_agent.db.base import get_engine
from cs_agent.rag.embeddings import EmbeddingProvider, format_vector
from cs_agent.settings import Settings, get_settings

Band = Literal["no_result", "low", "normal"]

_SEARCH = text(
    """
    SELECT policy_id, policy_version, chunk_index, anchor, content, metadata,
           1 - (embedding <=> CAST(:query AS vector)) AS score
    FROM agent.policy_chunks
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query AS vector)
    LIMIT :top_k
    """
)


class RetrievedChunk(BaseModel):
    """一条检索结果。字段够 `protocol.Citation` 与引用校验直接取用。"""

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: int
    chunk_index: int
    anchor: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    chunks: list[RetrievedChunk]
    max_score: float
    band: Band

    @property
    def policy_ids(self) -> list[str]:
        """本轮检索到的 policy_id（去重、保序），引用后置校验的输入（FR-304）。"""
        seen: list[str] = []
        for c in self.chunks:
            if c.policy_id not in seen:
                seen.append(c.policy_id)
        return seen


def classify_band(max_score: float, tau_low: float, tau_high: float) -> Band:
    """纯函数分带，方便不连库地测阈值边界。区间为左闭右开，与 PRD §11 一致。"""
    if tau_low > tau_high:
        raise ValueError(f"tau_low({tau_low}) 不得大于 tau_high({tau_high})")
    if max_score < tau_low:
        return "no_result"
    if max_score < tau_high:
        return "low"
    return "normal"


class PolicyRetriever:
    """策略检索器。`provider` 必须与 ingest 时用的是同一个，否则向量空间对不上。"""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        engine: Engine | None = None,
        settings: Settings | None = None,
        tau_low: float | None = None,
        tau_high: float | None = None,
        top_k: int | None = None,
    ) -> None:
        s = settings or get_settings()
        self._provider = provider
        self._engine = engine
        self.tau_low = s.rag_tau_low if tau_low is None else tau_low
        self.tau_high = s.rag_tau_high if tau_high is None else tau_high
        self.top_k = s.rag_top_k if top_k is None else top_k
        classify_band(0.0, self.tau_low, self.tau_high)  # 提前拒绝反了的阈值配置

    @property
    def engine(self) -> Engine:
        return self._engine or get_engine()

    def search(self, query: str, top_k: int | None = None) -> RetrievalResult:
        """向量检索 top-k 并分带。空库 / 空查询返回 `no_result`，绝不抛给调用方。"""
        limit = self.top_k if top_k is None else top_k
        vector = self._provider.embed_query(query)
        with self.engine.connect() as conn:
            rows = (
                conn.execute(_SEARCH, {"query": format_vector(vector), "top_k": limit})
                .mappings()
                .all()
            )

        chunks = [
            RetrievedChunk(
                policy_id=r["policy_id"],
                policy_version=r["policy_version"],
                chunk_index=r["chunk_index"],
                anchor=r["anchor"],
                content=r["content"],
                score=float(r["score"]),
                metadata=dict(r["metadata"] or {}),
            )
            for r in rows
        ]
        max_score = max((c.score for c in chunks), default=0.0)
        return RetrievalResult(
            query=query,
            chunks=chunks,
            max_score=max_score,
            band=classify_band(max_score, self.tau_low, self.tau_high),
        )
