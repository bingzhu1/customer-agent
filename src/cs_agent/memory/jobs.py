"""长期记忆的异步抽取（FR-704、PRD §7.5 `persist` 节点"投递异步记忆抽取任务"）。

FR-704 的两条要求：**不在请求热路径**、**失败不影响本轮响应**。这里的做法是
进程内线程池：`persist` 调 `submit()` 立即返回，抽取与写库在后台线程里跑，
任何异常都在线程里被吞掉并计数，绝不冒泡到对话流程。

为什么是进程内线程池，而不是任务表 + 独立 worker：
长期记忆是**非权威**的（ADR-0009）。进程重启丢掉一条"用户偏好短信通知"，
下次会话再抽一次就有了，且它影响不了任何判定。为它引入一张任务表、一个 worker
进程与一套重试语义，代价远大于收益。真正需要"绝不丢"的是退款这类外部副作用，
那条路走 transactional outbox（PRD §17），跟这里不是一回事。

**`drain()` 是给测试与 eval 用的**：抽取一旦真异步，`AgentUnderTest` 这种同步接口
就观察不到它了，MEM 类用例也就没法确定性断言。所以队列必须能被要求"等干净"。
生产路径不该调它——调了就等于又把抽取拖回热路径。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from uuid import UUID

import structlog

from cs_agent.memory.extract import MemoryCandidate, TranscriptTurn, extract_memories
from cs_agent.memory.user_memory import DEFAULT_TTL_DAYS, UserMemoryRepo

log = structlog.get_logger(__name__)

#: 只有一个 worker：抽取不赶时间，串行还能避免同一 `mem_key` 的并发 upsert 互相盖版本。
DEFAULT_MAX_WORKERS = 1
#: 低于这个置信度就不写库。记忆是给语气用的，宁缺毋滥。
DEFAULT_MIN_CONFIDENCE = 0.6

#: 抽取器的形状，便于测试注入替身而不必真的构造 LLM client。
Extractor = Callable[[Sequence[TranscriptTurn]], list[MemoryCandidate]]


@dataclass
class QueueStats:
    """轻量可观测。异常被吞掉了，就必须有别的地方能看出"吞了多少"。"""

    submitted: int = 0
    succeeded: int = 0
    failed: int = 0
    dropped: int = 0  # 队列已关闭后仍来的任务
    written: int = 0  # 实际写进 user_memory 的条数
    last_error: str | None = None

    def snapshot(self) -> dict[str, int | str | None]:
        return {
            "submitted": self.submitted,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dropped": self.dropped,
            "written": self.written,
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class ExtractionJob:
    """一次抽取任务。`transcript` 是纯文本对话，不带身份——身份靠 `user_id` 显式传。"""

    user_id: int
    transcript: tuple[TranscriptTurn, ...]
    source_thread_id: UUID | None = None
    now: datetime | None = None


@dataclass
class _Pending:
    futures: list[Future[None]] = field(default_factory=list)


class ExtractionQueue:
    """进程内异步抽取队列。构造即启动线程池，`close()` 或 `with` 退出时回收。

    典型用法（Phase 1 的 `persist` 节点）：

    ```python
    queue.submit(ExtractionJob(user_id=ctx.user_id, transcript=turns, source_thread_id=tid))
    # 立即返回，本轮响应不等它
    ```
    """

    def __init__(
        self,
        repo: UserMemoryRepo,
        *,
        extractor: Extractor = extract_memories,
        max_workers: int = DEFAULT_MAX_WORKERS,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(f"min_confidence 必须在 [0,1]，收到 {min_confidence}")
        self._repo = repo
        self._extractor = extractor
        self._min_confidence = min_confidence
        self._ttl_days = ttl_days
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mem-ex")
        self._lock = threading.Lock()
        self._pending = _Pending()
        self._closed = False
        self.stats = QueueStats()

    # --- 提交与回收 ---------------------------------------------------------

    def submit(self, job: ExtractionJob) -> None:
        """投递任务并立即返回。**任何情况下都不抛异常**——它挂在对话主路径上。"""
        with self._lock:
            if self._closed:
                self.stats.dropped += 1
                log.warning("memory_extraction_dropped", user_id=job.user_id, reason="closed")
                return
            if not job.transcript:
                return
            self.stats.submitted += 1
            future = self._executor.submit(self._run, job)
            self._pending.futures.append(future)

    def drain(self, timeout: float | None = 30.0) -> bool:
        """等队列跑干净。**只给测试与 eval 用**，生产路径调它等于取消了异步。

        返回是否在超时前跑完。已完成的 future 会被清掉，可反复调用。
        """
        with self._lock:
            futures = list(self._pending.futures)
        for future in futures:
            try:
                future.result(timeout=timeout)
            except TimeoutError:
                return False
            except Exception:  # noqa: BLE001  失败已在 `_run` 里计过数，这里只是不让它冒泡
                continue
        with self._lock:
            self._pending.futures = [f for f in self._pending.futures if not f.done()]
            return not self._pending.futures

    def close(self, *, wait: bool = True) -> None:
        """关闭队列。`wait=True` 时等在跑的任务结束，避免进程退出时写到一半。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> ExtractionQueue:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- 后台线程里跑的部分 -------------------------------------------------

    def _run(self, job: ExtractionJob) -> None:
        """抽取 + 写库。整段包在 try 里：后台线程的异常不该有任何外溢路径。"""
        try:
            candidates = self._extractor(job.transcript)
            written = 0
            for candidate in candidates:
                if candidate.confidence < self._min_confidence:
                    continue
                self._repo.upsert(
                    job.user_id,
                    candidate.mem_key,
                    candidate.mem_value,
                    confidence=candidate.confidence,
                    source_thread_id=job.source_thread_id,
                    ttl_days=self._ttl_days,
                    now=job.now,
                )
                written += 1
            with self._lock:
                self.stats.succeeded += 1
                self.stats.written += written
            log.info(
                "memory_extraction_done",
                user_id=job.user_id,
                candidates=len(candidates),
                written=written,
            )
        except Exception as exc:  # noqa: BLE001  FR-704：失败不影响本轮响应
            with self._lock:
                self.stats.failed += 1
                self.stats.last_error = f"{exc.__class__.__name__}: {exc}"
            log.warning("memory_extraction_failed", user_id=job.user_id, error=str(exc))


class InlineExtractionQueue:
    """同步执行的替身，接口与 `ExtractionQueue` 一致。

    给两种场景用：单测不想起线程；eval 需要"这一轮结束时记忆已经写好"的确定性。
    生产不要用它——那就把抽取放回热路径了。
    """

    def __init__(
        self,
        repo: UserMemoryRepo,
        *,
        extractor: Extractor = extract_memories,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> None:
        self._inner = ExtractionQueue(
            repo,
            extractor=extractor,
            max_workers=1,
            min_confidence=min_confidence,
            ttl_days=ttl_days,
        )

    @property
    def stats(self) -> QueueStats:
        return self._inner.stats

    def submit(self, job: ExtractionJob) -> None:
        if not job.transcript:
            return
        self._inner.stats.submitted += 1
        self._inner._run(job)  # noqa: SLF001  同一模块内的刻意复用，行为必须与异步版逐字一致

    def drain(self, timeout: float | None = None) -> bool:
        return True

    def close(self, *, wait: bool = True) -> None:
        self._inner.close(wait=wait)

    def __enter__(self) -> InlineExtractionQueue:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
