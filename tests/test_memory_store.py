"""`user_memory` / `memory_embeddings` / `case_state` 的读写（FR-705/706/709/710）。

连本 worktree 的开发库 `cs_agent_p2`，向量用 `FakeEmbeddings`（不触网）。
每个用例用独立的 user_id 与 thread_id，互不干扰，跑完自己清理。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from cs_agent.db.base import get_engine
from cs_agent.memory.case_facts import (
    ActionRecord,
    CaseFacts,
    Money,
    apply_action,
    apply_tool_result,
)
from cs_agent.memory.case_state import CaseStateRepo
from cs_agent.memory.compaction import ConversationWindow, Message, compact
from cs_agent.memory.extract import MemoryCandidate, TranscriptTurn
from cs_agent.memory.jobs import ExtractionJob, ExtractionQueue
from cs_agent.memory.user_memory import DEFAULT_TTL_DAYS, UserMemoryRepo
from cs_agent.rag.embeddings import FakeEmbeddings

PROVIDER = FakeEmbeddings()
NOW = datetime(2026, 9, 1, tzinfo=UTC)
#: 用高位 user_id，避开 seed 灌进 biz 的 101–118，防止跟别的测试互相看见对方的记忆。
BASE_USER = 990_000


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = get_engine()
    try:
        with eng.connect() as conn:
            dtype = conn.execute(
                text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_schema='agent' AND table_name='memory_embeddings' "
                    "AND column_name='embedding'"
                )
            ).scalar()
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过记忆存储测试：{exc.__class__.__name__}")
    if dtype != "vector":
        pytest.skip("memory_embeddings.embedding 还不是 vector，请先跑 make migrate")
    yield eng
    with eng.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM agent.memory_embeddings WHERE memory_id IN "
                "(SELECT id FROM agent.user_memory WHERE user_id >= :base)"
            ),
            {"base": BASE_USER},
        )
        conn.execute(
            text("DELETE FROM agent.user_memory WHERE user_id >= :base"), {"base": BASE_USER}
        )


@pytest.fixture
def repo(engine: Engine) -> UserMemoryRepo:
    return UserMemoryRepo(PROVIDER, engine=engine)


@pytest.fixture
def user(engine: Engine) -> Iterator[int]:
    """每个用例一个独立 user_id，用完把它的记忆删干净。"""
    uid = BASE_USER + int(uuid4().int % 100_000)
    yield uid
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM agent.memory_embeddings WHERE memory_id IN "
                "(SELECT id FROM agent.user_memory WHERE user_id = :uid)"
            ),
            {"uid": uid},
        )
        conn.execute(text("DELETE FROM agent.user_memory WHERE user_id = :uid"), {"uid": uid})


@pytest.fixture
def thread(engine: Engine) -> Iterator[UUID]:
    """case_state.thread_id 有指向 threads 的外键，先建一条线程。"""
    tid = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agent.threads (id, user_id, status, created_at, last_active_at) "
                "VALUES (:id, :uid, 'open', :now, :now)"
            ),
            {"id": tid, "uid": BASE_USER, "now": NOW},
        )
    yield tid
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM agent.case_state WHERE thread_id = :id"), {"id": tid})
        conn.execute(text("DELETE FROM agent.threads WHERE id = :id"), {"id": tid})


# --- user_memory：写入字段、版本、TTL（FR-705 / FR-710）----------------------


def test_upsert_records_confidence_source_and_ttl(repo: UserMemoryRepo, user: int) -> None:
    source = uuid4()
    rec = repo.upsert(
        user,
        "channel_preference",
        "希望短信通知",
        confidence=0.9,
        source_thread_id=source,
        now=NOW,
    )
    assert (rec.mem_value, rec.version) == ("希望短信通知", 1)
    assert rec.confidence == pytest.approx(0.9)
    assert rec.source_thread_id == source
    assert rec.ttl_at == NOW + timedelta(days=DEFAULT_TTL_DAYS)


def test_same_key_overwrites_and_bumps_version(repo: UserMemoryRepo, user: int) -> None:
    """FR-710：同 key 新值覆盖旧值并递增 version。"""
    repo.upsert(user, "language_preference", "中文", confidence=0.5, now=NOW)
    second = repo.upsert(user, "language_preference", "英文", confidence=0.7, now=NOW)
    assert (second.mem_value, second.version) == ("英文", 2)
    assert repo.get(user, "language_preference") == second


def test_different_keys_are_independent(repo: UserMemoryRepo, user: int) -> None:
    a = repo.upsert(user, "language_preference", "中文", confidence=0.5, now=NOW)
    b = repo.upsert(user, "channel_preference", "短信", confidence=0.5, now=NOW)
    assert a.id != b.id and a.version == b.version == 1


def test_confidence_out_of_range_is_rejected(repo: UserMemoryRepo, user: int) -> None:
    with pytest.raises(ValueError):
        repo.upsert(user, "k", "v", confidence=1.5, now=NOW)


def test_ttl_days_zero_means_no_expiry(repo: UserMemoryRepo, user: int) -> None:
    rec = repo.upsert(user, "k", "v", confidence=0.5, ttl_days=0, now=NOW)
    assert rec.ttl_at is None


# --- 检索（FR-706）----------------------------------------------------------


def test_search_returns_own_memories_ranked(repo: UserMemoryRepo, user: int) -> None:
    repo.upsert(user, "channel_preference", "希望通过短信接收通知", confidence=0.9, now=NOW)
    repo.upsert(user, "communication_style", "回复请尽量简短", confidence=0.6, now=NOW)
    out = repo.search(user, "短信通知偏好", top_k=5, now=NOW)
    assert [m.mem_key for m in out][0] == "channel_preference"
    assert all(m.score is not None for m in out)
    assert [m.score for m in out] == sorted((m.score or 0 for m in out), reverse=True)


def test_search_never_crosses_users(repo: UserMemoryRepo, user: int, engine: Engine) -> None:
    other = user + 1
    repo.upsert(other, "channel_preference", "希望通过短信接收通知", confidence=0.9, now=NOW)
    try:
        assert repo.search(user, "短信通知偏好", now=NOW) == []
    finally:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM agent.memory_embeddings WHERE memory_id IN "
                    "(SELECT id FROM agent.user_memory WHERE user_id = :uid)"
                ),
                {"uid": other},
            )
            conn.execute(text("DELETE FROM agent.user_memory WHERE user_id = :uid"), {"uid": other})


def test_soft_deleted_memory_is_not_retrievable(repo: UserMemoryRepo, user: int) -> None:
    """FR-706：删除后不再被检索，但行还在，便于审计。"""
    repo.upsert(user, "channel_preference", "希望通过短信接收通知", confidence=0.9, now=NOW)
    assert repo.delete(user, "channel_preference", now=NOW) is True
    assert repo.search(user, "短信通知偏好", now=NOW) == []
    assert repo.get(user, "channel_preference") is not None
    assert repo.delete(user, "channel_preference", now=NOW) is False


def test_upsert_does_not_revive_a_deleted_memory(repo: UserMemoryRepo, user: int) -> None:
    """删除是用户意愿，下一次抽取不得把它悄悄复活。"""
    repo.upsert(user, "channel_preference", "希望通过短信接收通知", confidence=0.9, now=NOW)
    repo.delete(user, "channel_preference", now=NOW)
    repo.upsert(user, "channel_preference", "希望通过短信接收通知", confidence=0.95, now=NOW)
    assert repo.search(user, "短信通知偏好", now=NOW) == []


def test_expired_memory_is_not_retrievable(repo: UserMemoryRepo, user: int) -> None:
    repo.upsert(
        user, "channel_preference", "希望通过短信接收通知", confidence=0.9, ttl_days=1, now=NOW
    )
    assert repo.search(user, "短信通知偏好", now=NOW) != []
    assert repo.search(user, "短信通知偏好", now=NOW + timedelta(days=2)) == []


def test_renew_extends_ttl(repo: UserMemoryRepo, user: int) -> None:
    repo.upsert(
        user, "channel_preference", "希望通过短信接收通知", confidence=0.9, ttl_days=1, now=NOW
    )
    later = NOW + timedelta(days=2)
    assert repo.renew(user, ["channel_preference"], ttl_days=30, now=NOW) == 1
    assert repo.search(user, "短信通知偏好", now=later) != []
    assert repo.renew(user, [], now=NOW) == 0


def test_search_has_no_side_effect_on_ttl(repo: UserMemoryRepo, user: int) -> None:
    """续期是显式的 renew；读接口不写库。"""
    rec = repo.upsert(user, "channel_preference", "希望通过短信接收通知", confidence=0.9, now=NOW)
    repo.search(user, "短信通知偏好", now=NOW)
    assert repo.get(user, "channel_preference") == rec


def test_embedding_row_is_replaced_not_duplicated(
    repo: UserMemoryRepo, user: int, engine: Engine
) -> None:
    rec = repo.upsert(user, "k", "第一版", confidence=0.5, now=NOW)
    repo.upsert(user, "k", "第二版", confidence=0.5, now=NOW)
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM agent.memory_embeddings WHERE memory_id = :id"),
            {"id": rec.id},
        ).scalar_one()
    assert count == 1


# --- case_state（FR-709、不变式 3）------------------------------------------


def test_case_facts_roundtrip(engine: Engine, thread: UUID) -> None:
    repo = CaseStateRepo(engine=engine)
    assert repo.load_facts(thread) == CaseFacts()

    facts = apply_action(
        apply_tool_result(CaseFacts(), "get_order", {"order_id": 82913, "total_amount": "89.00"}),
        ActionRecord(
            action_id="a1",
            action_type="refund",
            status="proposed",
            amount=Money(amount=Decimal("89.00"), source="order.82913.total_amount"),
        ),
    )
    repo.save_facts(thread, facts, now=NOW)
    assert repo.load_facts(thread) == facts


def test_narrative_compression_never_touches_case_facts(engine: Engine, thread: UUID) -> None:
    """不变式 3：压缩只作用于叙述，CaseFacts 与 pending_action 原样保留。"""
    repo = CaseStateRepo(engine=engine)
    facts = apply_action(
        CaseFacts(), ActionRecord(action_id="a1", action_type="refund", status="proposed")
    )
    repo.save_facts(thread, facts, now=NOW)

    repo.save_narrative(thread, "用户咨询了退款。", now=NOW)
    repo.save_narrative(thread, "用户咨询了退款，随后确认。", now=NOW)

    assert repo.load_facts(thread) == facts
    assert repo.load_facts(thread).pending_action is not None
    summary, version = repo.load_narrative(thread)
    assert summary == "用户咨询了退款，随后确认。"
    assert version == 2, "summary_version 恒等于叙述被写过的次数"


def test_compaction_result_persists_without_touching_case_facts(
    engine: Engine, thread: UUID
) -> None:
    """FR-703 端到端：压缩产出的摘要落盘后，CaseFacts 与 pending_action 一字未动。"""
    repo = CaseStateRepo(engine=engine)
    facts = apply_action(
        apply_tool_result(CaseFacts(), "get_order", {"order_id": 82913, "total_amount": "89.00"}),
        ActionRecord(action_id="a1", action_type="refund", status="proposed"),
    )
    repo.save_facts(thread, facts, now=NOW)

    class _Summarizer:
        def summarize(self, previous_summary: str | None, messages: object) -> str:
            return "用户咨询了 82913 的退款，尚未确认。"

    win = ConversationWindow(
        messages=tuple(
            Message(role="user" if i % 2 == 0 else "assistant", text="话" * 40) for i in range(20)
        )
    )
    result = compact(win, _Summarizer(), threshold=50, keep_recent=6)
    assert result.compacted is True
    repo.save_narrative(thread, result.window.narrative_summary or "", now=NOW)

    assert repo.load_facts(thread) == facts
    assert repo.load_facts(thread).pending_action is not None
    assert repo.load_narrative(thread)[0] == "用户咨询了 82913 的退款，尚未确认。"


def test_narrative_before_facts_does_not_lose_facts(engine: Engine, thread: UUID) -> None:
    repo = CaseStateRepo(engine=engine)
    repo.save_narrative(thread, "先有叙述", now=NOW)
    facts = apply_tool_result(CaseFacts(), "get_order", {"order_id": 82913})
    repo.save_facts(thread, facts, now=NOW)
    assert repo.load_facts(thread) == facts
    assert repo.load_narrative(thread)[0] == "先有叙述"


# --- 异步抽取端到端（FR-704 + FR-705）---------------------------------------


def test_async_extraction_writes_real_rows(repo: UserMemoryRepo, user: int) -> None:
    """队列跑完后，抽出来的候选真的落进了 user_memory，且带齐来源与 TTL。"""
    thread_id = uuid4()

    def extractor(_: object) -> list[MemoryCandidate]:
        return [
            MemoryCandidate(
                mem_key="channel_preference",
                mem_value="希望通过短信接收通知",
                category="channel_preference",
                confidence=0.9,
            ),
            MemoryCandidate(
                mem_key="communication_style",
                mem_value="回复请尽量简短",
                category="communication_style",
                confidence=0.2,  # 低于阈值，不该写
            ),
        ]

    with ExtractionQueue(repo, extractor=extractor) as queue:
        queue.submit(
            ExtractionJob(
                user_id=user,
                transcript=(TranscriptTurn(role="user", text="以后用短信通知我"),),
                source_thread_id=thread_id,
                now=NOW,
            )
        )
        assert queue.drain(timeout=10) is True

    written = repo.get(user, "channel_preference")
    assert written is not None
    assert written.mem_value == "希望通过短信接收通知"
    assert written.source_thread_id == thread_id
    assert written.ttl_at == NOW + timedelta(days=DEFAULT_TTL_DAYS)
    assert repo.get(user, "communication_style") is None, "低置信候选不该写库"
    assert repo.search(user, "短信通知", now=NOW)[0].mem_key == "channel_preference"


def test_async_extraction_survives_a_broken_extractor(repo: UserMemoryRepo, user: int) -> None:
    """FR-704：抽取失败不得影响调用方，只体现在 stats 上。"""

    def boom(_: object) -> list[MemoryCandidate]:
        raise RuntimeError("模型挂了")

    with ExtractionQueue(repo, extractor=boom) as queue:
        queue.submit(
            ExtractionJob(
                user_id=user,
                transcript=(TranscriptTurn(role="user", text="随便说说"),),
                now=NOW,
            )
        )
        queue.drain(timeout=10)
        assert queue.stats.failed == 1
    assert repo.search(user, "随便说说", now=NOW) == []


def test_search_always_injects_language_preference(repo: UserMemoryRepo, user: int) -> None:
    """语言偏好与当前问句不相似也必须在结果里：它对每一轮都有效（否则记忆多了就被挤掉）。"""
    repo.upsert(user, "language_preference", "希望客服用英文沟通", confidence=0.95, now=NOW)
    for i in range(4):
        repo.upsert(user, f"topic_{i}", f"物流催单相关记录 {i}", confidence=0.7, now=NOW)
    out = repo.search(user, "物流催单相关", top_k=2, now=NOW)
    keys = [m.mem_key for m in out]
    assert "language_preference" in keys
    assert len(out) == 3  # top_k 2 + 追加 1
    assert [m.score for m in out] == sorted((m.score or 0 for m in out), reverse=True)
    # 关掉固定注入就回到纯 top_k
    assert "language_preference" not in [
        m.mem_key for m in repo.search(user, "物流催单相关", top_k=2, now=NOW, pinned_keys=())
    ]


def test_upsert_after_deletion_revives_the_key(repo: UserMemoryRepo, user: int) -> None:
    """删除之后用户再次表达同一偏好，是新的意愿，必须能学回来（否则该 key 永远死掉）。"""
    from datetime import timedelta

    repo.upsert(user, "language_preference", "希望客服用英文沟通", confidence=0.9, now=NOW)
    repo.delete(user, "language_preference", now=NOW)
    later = NOW + timedelta(seconds=30)
    rec = repo.upsert(user, "language_preference", "希望客服用英文沟通", confidence=0.95, now=later)
    assert rec.version == 2
    assert "language_preference" in [m.mem_key for m in repo.search(user, "语言", now=later)]
