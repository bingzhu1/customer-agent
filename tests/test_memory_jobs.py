"""异步记忆抽取队列（FR-704）。不触网：extractor 与 repo 都用替身。"""

from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID, uuid4

import pytest

from cs_agent.memory.extract import MemoryCandidate, TranscriptTurn
from cs_agent.memory.jobs import (
    DEFAULT_MIN_CONFIDENCE,
    ExtractionJob,
    ExtractionQueue,
    InlineExtractionQueue,
)

TRANSCRIPT = (
    TranscriptTurn(role="user", text="以后用短信通知我吧"),
    TranscriptTurn(role="assistant", text="好的。"),
)


def candidate(key: str = "channel_preference", confidence: float = 0.9) -> MemoryCandidate:
    return MemoryCandidate(
        mem_key=key, mem_value="希望短信通知", category="channel_preference", confidence=confidence
    )


class _FakeRepo:
    """记录 upsert 调用；可选地抛异常，用来验证失败被吞掉。"""

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise = raise_exc
        self._lock = threading.Lock()

    def upsert(self, user_id: int, mem_key: str, mem_value: str, **kwargs: Any) -> Any:
        if self._raise is not None:
            raise self._raise
        with self._lock:
            self.calls.append(
                {"user_id": user_id, "mem_key": mem_key, "mem_value": mem_value, **kwargs}
            )
        return None


def _queue(repo: Any, extractor: Any, **kw: Any) -> ExtractionQueue:
    return ExtractionQueue(repo, extractor=extractor, **kw)


# --- 基本行为 ---------------------------------------------------------------


def test_submitted_candidates_are_written() -> None:
    repo, tid = _FakeRepo(), uuid4()
    with _queue(repo, lambda t: [candidate()]) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT, source_thread_id=tid))
        assert q.drain() is True
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert (call["user_id"], call["mem_key"]) == (101, "channel_preference")
    assert call["source_thread_id"] == tid
    assert call["confidence"] == pytest.approx(0.9)


def test_submit_returns_before_the_work_finishes() -> None:
    """FR-704 的"不在请求热路径"：submit 不等抽取跑完。"""
    started = threading.Event()
    release = threading.Event()

    def slow(_: Any) -> list[MemoryCandidate]:
        started.set()
        release.wait(timeout=5)
        return [candidate()]

    repo = _FakeRepo()
    with _queue(repo, slow) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        assert started.wait(timeout=5), "任务应该已经在后台跑起来了"
        assert repo.calls == [], "submit 不该等它写完"
        release.set()
        q.drain()
    assert len(repo.calls) == 1


def test_extractor_receives_the_transcript() -> None:
    seen: list[Any] = []
    with _queue(_FakeRepo(), lambda t: seen.append(list(t)) or []) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        q.drain()
    assert seen == [list(TRANSCRIPT)]


def test_empty_transcript_is_not_submitted() -> None:
    calls: list[Any] = []
    with _queue(_FakeRepo(), lambda t: calls.append(t) or []) as q:
        q.submit(ExtractionJob(user_id=101, transcript=()))
        q.drain()
    assert calls == []
    assert q.stats.submitted == 0


# --- FR-704：失败不影响本轮响应 ---------------------------------------------


def test_extractor_exception_never_escapes() -> None:
    def boom(_: Any) -> list[MemoryCandidate]:
        raise RuntimeError("模型挂了")

    with _queue(_FakeRepo(), boom) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))  # 不得抛
        q.drain()
    assert q.stats.failed == 1
    assert q.stats.succeeded == 0
    assert "RuntimeError" in (q.stats.last_error or "")


def test_repo_exception_never_escapes() -> None:
    repo = _FakeRepo(raise_exc=ConnectionError("数据库不可用"))
    with _queue(repo, lambda t: [candidate()]) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        q.drain()
    assert q.stats.failed == 1
    assert "ConnectionError" in (q.stats.last_error or "")


def test_one_failure_does_not_block_later_jobs() -> None:
    calls: list[int] = []

    def flaky(_: Any) -> list[MemoryCandidate]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("第一次失败")
        return [candidate()]

    repo = _FakeRepo()
    with _queue(repo, flaky) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        q.submit(ExtractionJob(user_id=102, transcript=TRANSCRIPT))
        q.drain()
    assert (q.stats.failed, q.stats.succeeded) == (1, 1)
    assert [c["user_id"] for c in repo.calls] == [102]


def test_stats_snapshot_exposes_swallowed_failures() -> None:
    """异常被吞掉了，就必须有地方看得出吞了多少。"""

    def boom(_: Any) -> list[MemoryCandidate]:
        raise RuntimeError("x")

    with _queue(_FakeRepo(), boom) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        q.drain()
        snap = q.stats.snapshot()
    assert snap["submitted"] == 1 and snap["failed"] == 1 and snap["written"] == 0


# --- 置信度阈值 -------------------------------------------------------------


def test_low_confidence_candidates_are_not_written() -> None:
    repo = _FakeRepo()
    low = candidate("communication_style", DEFAULT_MIN_CONFIDENCE - 0.01)
    high = candidate("channel_preference", DEFAULT_MIN_CONFIDENCE)
    with _queue(repo, lambda t: [low, high]) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        q.drain()
    assert [c["mem_key"] for c in repo.calls] == ["channel_preference"]
    assert q.stats.written == 1


def test_min_confidence_is_configurable_and_validated() -> None:
    repo = _FakeRepo()
    with _queue(repo, lambda t: [candidate(confidence=0.2)], min_confidence=0.1) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        q.drain()
    assert len(repo.calls) == 1
    with pytest.raises(ValueError):
        _queue(_FakeRepo(), lambda t: [], min_confidence=1.5)


def test_ttl_days_is_passed_through() -> None:
    repo = _FakeRepo()
    with _queue(repo, lambda t: [candidate()], ttl_days=30) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        q.drain()
    assert repo.calls[0]["ttl_days"] == 30


# --- drain / close ----------------------------------------------------------


def test_drain_waits_for_everything() -> None:
    repo = _FakeRepo()

    def slow(_: Any) -> list[MemoryCandidate]:
        time.sleep(0.02)
        return [candidate()]

    with _queue(repo, slow) as q:
        for uid in range(101, 106):
            q.submit(ExtractionJob(user_id=uid, transcript=TRANSCRIPT))
        assert q.drain() is True
    assert len(repo.calls) == 5


def test_drain_is_idempotent() -> None:
    with _queue(_FakeRepo(), lambda t: [candidate()]) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        assert q.drain() is True
        assert q.drain() is True


def test_submit_after_close_is_dropped_not_raised() -> None:
    repo = _FakeRepo()
    q = _queue(repo, lambda t: [candidate()])
    q.close()
    q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))  # 不得抛
    assert q.stats.dropped == 1
    assert repo.calls == []


def test_close_is_idempotent() -> None:
    q = _queue(_FakeRepo(), lambda t: [])
    q.close()
    q.close()


def test_close_waits_for_running_jobs_by_default() -> None:
    repo = _FakeRepo()

    def slow(_: Any) -> list[MemoryCandidate]:
        time.sleep(0.05)
        return [candidate()]

    q = _queue(repo, slow)
    q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
    q.close()
    assert len(repo.calls) == 1, "close(wait=True) 不该把写到一半的任务丢掉"


# --- 同步替身 ---------------------------------------------------------------


def test_inline_queue_writes_synchronously() -> None:
    """eval 需要"这一轮结束时记忆已经写好"，所以给一个同步替身。"""
    repo = _FakeRepo()
    with InlineExtractionQueue(repo, extractor=lambda t: [candidate()]) as q:  # type: ignore[arg-type]
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
        assert len(repo.calls) == 1, "submit 返回时就该写完了"
        assert q.drain() is True
    assert q.stats.written == 1


def test_inline_queue_swallows_failures_the_same_way() -> None:
    def boom(_: Any) -> list[MemoryCandidate]:
        raise RuntimeError("x")

    with InlineExtractionQueue(_FakeRepo(), extractor=boom) as q:  # type: ignore[arg-type]
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
    assert q.stats.failed == 1


def test_inline_queue_respects_confidence_threshold() -> None:
    repo = _FakeRepo()
    with InlineExtractionQueue(  # type: ignore[arg-type]
        repo, extractor=lambda t: [candidate(confidence=0.1)]
    ) as q:
        q.submit(ExtractionJob(user_id=101, transcript=TRANSCRIPT))
    assert repo.calls == []


# --- 结构约束 ---------------------------------------------------------------


def test_job_carries_identity_explicitly_not_from_transcript() -> None:
    """身份靠 user_id 显式传，不从对话文本里猜（红线 1 的同一条思路）。"""
    assert set(ExtractionJob.__dataclass_fields__) == {
        "user_id",
        "transcript",
        "source_thread_id",
        "now",
    }


def test_module_does_not_import_policy_or_decision() -> None:
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "cs_agent" / "memory" / "jobs.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any(m.startswith(("cs_agent.policy", "cs_agent.decision")) for m in imported)


def test_source_thread_id_type_is_uuid() -> None:
    job = ExtractionJob(user_id=101, transcript=TRANSCRIPT, source_thread_id=uuid4())
    assert isinstance(job.source_thread_id, UUID)
