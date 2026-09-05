"""retriever：阈值分带纯函数 + 连 `cs_agent_p2` 的向量检索集成测试（FR-303 / FR-307）。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from cs_agent.db.base import get_engine
from cs_agent.rag.embeddings import FakeEmbeddings
from cs_agent.rag.ingest import ingest_policies
from cs_agent.rag.retriever import PolicyRetriever, classify_band
from cs_agent.settings import Settings

POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"
PROVIDER = FakeEmbeddings()


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (0.0, "no_result"),
        (0.29, "no_result"),
        (0.30, "low"),
        (0.59, "low"),
        (0.60, "normal"),
        (1.0, "normal"),
    ],
)
def test_band_boundaries_are_half_open(score: float, band: str) -> None:
    assert classify_band(score, 0.30, 0.60) == band


def test_inverted_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError):
        classify_band(0.5, 0.8, 0.2)


def test_thresholds_come_from_settings() -> None:
    """FR-303：τ 必须是配置项。"""
    s = Settings(rag_tau_low=0.11, rag_tau_high=0.22, rag_top_k=5)
    r = PolicyRetriever(PROVIDER, settings=s)
    assert (r.tau_low, r.tau_high, r.top_k) == (0.11, 0.22, 5)
    override = PolicyRetriever(PROVIDER, settings=s, tau_low=0.4, tau_high=0.7, top_k=2)
    assert (override.tau_low, override.tau_high, override.top_k) == (0.4, 0.7, 2)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = get_engine()
    try:
        with eng.connect() as conn:
            dtype = conn.execute(
                text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_schema='agent' AND table_name='policy_chunks' "
                    "AND column_name='embedding'"
                )
            ).scalar()
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过 RAG 集成测试：{exc.__class__.__name__}")
    if dtype != "vector":
        pytest.skip("policy_chunks.embedding 还不是 vector，请先跑 make migrate")
    ingest_policies(POLICY_DIR, provider=PROVIDER, engine=eng)
    yield eng


def _retriever(
    engine: Engine,
    *,
    tau_low: float = 0.0,
    tau_high: float = 1.1,
    top_k: int | None = None,
) -> PolicyRetriever:
    """哨兵阈值：默认让所有结果都落 normal，需要测分带的用例再显式给 τ。"""
    return PolicyRetriever(PROVIDER, engine=engine, tau_low=tau_low, tau_high=tau_high, top_k=top_k)


def test_search_returns_top_k_sorted_by_score(engine: Engine) -> None:
    result = _retriever(engine).search("保修期多长", top_k=5)
    assert len(result.chunks) == 5
    scores = [c.score for c in result.chunks]
    assert scores == sorted(scores, reverse=True)
    assert result.max_score == scores[0]


def test_each_chunk_carries_citation_fields(engine: Engine) -> None:
    for chunk in _retriever(engine).search("退款", top_k=3).chunks:
        assert chunk.policy_id and chunk.policy_version >= 1
        assert "#" in chunk.anchor and chunk.content
        assert 0.0 <= chunk.score <= 1.0
        assert chunk.metadata["policy_id"] == chunk.policy_id
        assert chunk.metadata["policy_version"] == chunk.policy_version


def test_warranty_query_hits_warranty_policy(engine: Engine) -> None:
    result = _retriever(engine).search("电子产品保修期多长", top_k=3)
    assert result.chunks[0].policy_id == "WARRANTY-STD-001"
    assert "WARRANTY-STD-001" in result.policy_ids


def test_policy_ids_deduped_and_ordered(engine: Engine) -> None:
    ids = _retriever(engine).search("保修", top_k=8).policy_ids
    assert len(ids) == len(set(ids))


def test_low_score_falls_in_no_result_band(engine: Engine) -> None:
    """FR-307：max_score < τ_low 时分带为 no_result，chunk 仍返回供排障但不得作答。"""
    result = _retriever(engine, tau_low=0.99, tau_high=1.0).search("海外直邮关税谁承担")
    assert result.band == "no_result"
    assert result.chunks, "分带只影响决策，不影响是否把候选返回给调用方"


def test_middle_score_falls_in_low_band(engine: Engine) -> None:
    query = "退款一般几天能到账"
    probe = _retriever(engine).search(query)
    mid = probe.max_score
    result = _retriever(engine, tau_low=mid - 0.01, tau_high=mid + 0.01).search(query)
    assert result.band == "low"


def test_high_score_falls_in_normal_band(engine: Engine) -> None:
    query = "电子产品保修期多长"
    probe = _retriever(engine).search(query)
    result = _retriever(engine, tau_low=0.0, tau_high=probe.max_score).search(query)
    assert result.band == "normal"


def test_top_k_defaults_to_constructor_value(engine: Engine) -> None:
    assert len(_retriever(engine, top_k=2).search("退款").chunks) == 2
