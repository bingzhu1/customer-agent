"""`ActionService`：落库、确认、幂等执行、过期、归属、审计（FR-502~508）。

连 `.env` 的 DATABASE_URL（本 worktree 为 cs_agent_p3）。用例自建自清：
每个用例一个新 thread，结束时删掉自己写的 agent_actions / audit_log / refunds，不跑 seed。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from cs_agent.actions import (
    ActionExpiredError,
    ActionNotFoundError,
    ActionProposal,
    ActionService,
    ActionStateError,
    ActionStatus,
    ActionType,
)
from cs_agent.auth.context import AuthContext
from cs_agent.db.models.agent import AgentAction, AuditLog, Thread
from cs_agent.db.models.biz import Refund
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.policy.engine import PolicyOutcome, PolicyVerdict
from cs_agent.services.refund import RefundService
from cs_agent.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ORDER = 82913  # 契约 §2：89.00，seed 里没有退款记录
OWNER = 101
STRANGER = 202  # 契约 §1：王芳，持有 90210
AMOUNT = Decimal("89.00")
T0 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

ALLOW = PolicyVerdict(
    outcome=PolicyOutcome.ALLOW,
    reason_code=ReasonCode.POLICY_SATISFIED,
    policy_id="REFUND-STD-001",
    policy_version=3,
    max_auto_amount=Decimal("200"),
)
PROPOSAL = ActionProposal(
    ActionType.REFUND,
    {"order_id": TEST_ORDER, "amount": AMOUNT, "reason": "商品未使用"},
)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    return eng


class Clock:
    """可推进的时钟。过期用例不需要等 24 小时。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass
class Harness:
    factory: sessionmaker[Session]
    service: ActionService
    clock: Clock
    thread_id: UUID
    ctx: AuthContext

    def refund_count(self) -> int:
        with self.factory() as s:
            return int(
                s.scalar(
                    select(func.count()).select_from(Refund).where(Refund.order_id == TEST_ORDER)
                )
                or 0
            )

    def refunds(self) -> list[Refund]:
        with self.factory() as s:
            return list(s.scalars(select(Refund).where(Refund.order_id == TEST_ORDER)))

    def action_count(self) -> int:
        with self.factory() as s:
            return int(
                s.scalar(
                    select(func.count())
                    .select_from(AgentAction)
                    .where(AgentAction.thread_id == self.thread_id)
                )
                or 0
            )

    def audit(self) -> list[AuditLog]:
        with self.factory() as s:
            return list(
                s.scalars(
                    select(AuditLog)
                    .where(AuditLog.thread_id == self.thread_id)
                    .order_by(AuditLog.id)
                )
            )

    def events(self) -> list[str]:
        return [row.event_type for row in self.audit()]

    def status(self, action_id: int) -> ActionStatus:
        with self.factory() as s:
            row = s.get(AgentAction, action_id)
            assert row is not None
            return ActionStatus(row.status)


def _make_harness(engine: Engine, **kwargs: object) -> Iterator[Harness]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    thread_id = uuid4()
    with factory() as s, s.begin():
        s.execute(delete(Refund).where(Refund.order_id == TEST_ORDER))
        s.add(Thread(id=thread_id, user_id=OWNER, status="open", created_at=T0, last_active_at=T0))
    clock = Clock(T0)
    service = ActionService(factory, clock=clock, **kwargs)  # type: ignore[arg-type]
    yield Harness(factory, service, clock, thread_id, AuthContext.of(OWNER))
    with factory() as s, s.begin():
        s.execute(delete(AuditLog).where(AuditLog.thread_id == thread_id))
        s.execute(delete(AgentAction).where(AgentAction.thread_id == thread_id))
        s.execute(delete(Thread).where(Thread.id == thread_id))
        s.execute(delete(Refund).where(Refund.order_id == TEST_ORDER))


@pytest.fixture
def h(engine: Engine) -> Iterator[Harness]:
    yield from _make_harness(engine)


def propose(h: Harness, outcome: DecisionOutcome = DecisionOutcome.REQUIRE_CONFIRMATION) -> int:
    return h.service.propose(
        h.ctx, h.thread_id, PROPOSAL, outcome=outcome, verdict=ALLOW, window_start=T0
    ).id


# --- 提议 -----------------------------------------------------------------------


def test_propose_writes_one_row_with_key_and_expiry(h: Harness) -> None:
    record = h.service.propose(
        h.ctx, h.thread_id, PROPOSAL, outcome=DecisionOutcome.REQUIRE_CONFIRMATION, verdict=ALLOW
    )
    assert record.status is ActionStatus.AWAITING_CONFIRMATION
    assert len(record.idempotency_key) == 64
    assert record.expires_at == T0 + timedelta(hours=24)  # FR-504
    assert (record.policy_id, record.policy_version) == ("REFUND-STD-001", 3)
    assert record.params == {"order_id": TEST_ORDER, "amount": "89", "reason": "商品未使用"}
    assert h.action_count() == 1
    assert h.events() == ["action_proposed"]


@pytest.mark.parametrize(
    ("outcome", "status", "has_expiry"),
    [
        (DecisionOutcome.REQUIRE_CONFIRMATION, ActionStatus.AWAITING_CONFIRMATION, True),
        (DecisionOutcome.REQUIRE_HUMAN, ActionStatus.AWAITING_HUMAN, True),
        (DecisionOutcome.DENY, ActionStatus.REJECTED, False),
    ],
)
def test_propose_maps_the_decision_outcome(
    h: Harness, outcome: DecisionOutcome, status: ActionStatus, has_expiry: bool
) -> None:
    record = h.service.propose(h.ctx, h.thread_id, PROPOSAL, outcome=outcome, verdict=ALLOW)
    assert record.status is status
    assert (record.expires_at is not None) is has_expiry


def test_propose_rejects_outcomes_that_produce_no_action(h: Harness) -> None:
    with pytest.raises(ActionStateError):
        h.service.propose(h.ctx, h.thread_id, PROPOSAL, outcome=DecisionOutcome.ANSWER)


def test_repeated_propose_hits_the_unique_key_and_returns_the_same_row(h: Harness) -> None:
    """图重放（ADR-0003）与用户连点都走这条路：唯一索引拦下，不新建第二行。"""
    first = propose(h)
    second = propose(h)
    assert first == second
    assert h.action_count() == 1
    assert h.events() == ["action_proposed", "action_proposal_replayed"]


def test_different_params_produce_a_different_action(h: Harness) -> None:
    """FR-605：改了金额就是另一笔动作。"""
    original = h.service.get(propose(h), h.ctx)
    edited = ActionProposal(
        ActionType.REFUND, {"order_id": TEST_ORDER, "amount": Decimal("50.00"), "reason": "改主意"}
    )
    other = h.service.propose(
        h.ctx,
        h.thread_id,
        edited,
        outcome=DecisionOutcome.REQUIRE_CONFIRMATION,
        verdict=ALLOW,
        window_start=T0,
    )
    assert h.action_count() == 2
    assert other.id != original.id
    assert other.idempotency_key != original.idempotency_key


# --- 确认与幂等执行 --------------------------------------------------------------


def test_confirm_executes_once_and_records_the_result(h: Harness) -> None:
    action_id = propose(h)
    outcome = h.service.confirm(action_id, h.ctx)

    assert outcome.replay is False
    assert outcome.action.status is ActionStatus.SUCCEEDED
    assert outcome.reason_code is ReasonCode.POLICY_SATISFIED
    assert h.refund_count() == 1
    refund = h.refunds()[0]
    assert outcome.action.result == {
        "refund_id": refund.id,
        "amount": "89.00",
        "status": "succeeded",
        "simulated": True,
    }
    assert h.events() == ["action_proposed", "action_executed"]


def test_repeated_confirm_executes_only_once(h: Harness) -> None:
    """FR-503：第二次确认返回原结果，不产生第二条退款。"""
    action_id = propose(h)
    first = h.service.confirm(action_id, h.ctx)
    second = h.service.confirm(action_id, h.ctx)

    assert (first.replay, second.replay) == (False, True)
    assert second.action.result == first.action.result
    assert second.reason_code is ReasonCode.IDEMPOTENT_REPLAY
    assert h.refund_count() == 1
    assert h.events() == ["action_proposed", "action_executed", "action_execution_replayed"]


def test_concurrent_confirm_produces_exactly_one_refund(h: Harness) -> None:
    """两个线程同时确认：行锁串行化，恰好一次执行、一次重放，只有一条 biz.refunds。"""
    action_id = propose(h)
    barrier = threading.Barrier(2)

    def run() -> bool:
        barrier.wait(timeout=10)
        return h.service.confirm(action_id, h.ctx).replay

    with ThreadPoolExecutor(max_workers=2) as pool:
        replays = sorted(f.result(timeout=30) for f in [pool.submit(run), pool.submit(run)])

    assert replays == [False, True]
    assert h.refund_count() == 1
    assert h.status(action_id) is ActionStatus.SUCCEEDED
    assert h.events().count("action_executed") == 1


def test_execution_uses_the_authenticated_owner_not_the_params(h: Harness) -> None:
    """红线 1：退款落到 AuthContext 的用户名下，params 里根本没有身份字段。"""
    h.service.confirm(propose(h), h.ctx)
    assert h.refunds()[0].user_id == OWNER


def test_confirm_on_a_succeeded_action_never_touches_the_business_db(h: Harness) -> None:
    action_id = propose(h)
    h.service.confirm(action_id, h.ctx)
    before = h.refunds()[0].id
    h.service.confirm(action_id, h.ctx)
    assert [r.id for r in h.refunds()] == [before]


# --- 过期（FR-504）---------------------------------------------------------------


def test_expired_action_is_refused_and_marked(h: Harness) -> None:
    action_id = propose(h)
    h.clock.advance(timedelta(hours=25))
    with pytest.raises(ActionExpiredError):
        h.service.confirm(action_id, h.ctx)
    assert h.status(action_id) is ActionStatus.EXPIRED
    assert h.events() == ["action_proposed", "action_expired"]
    assert h.refund_count() == 0


def test_action_just_before_expiry_still_works(h: Harness) -> None:
    action_id = propose(h)
    h.clock.advance(timedelta(hours=23, minutes=59))
    assert h.service.confirm(action_id, h.ctx).replay is False


# --- 归属与存在性（FR-505、FR-804）------------------------------------------------


def test_other_users_action_and_missing_action_raise_the_same_error(h: Harness) -> None:
    """他人的动作与根本不存在的 id 抛同一个异常：无法通过枚举 action_id 探测别人的退款。"""
    action_id = propose(h)
    stranger = AuthContext.of(STRANGER)

    with pytest.raises(ActionNotFoundError) as theirs:
        h.service.confirm(action_id, stranger)
    with pytest.raises(ActionNotFoundError) as missing:
        h.service.confirm(action_id + 10_000_000, stranger)

    assert type(theirs.value) is type(missing.value)
    assert h.refund_count() == 0


def test_get_is_scoped_to_the_owner(h: Harness) -> None:
    action_id = propose(h)
    assert h.service.get(action_id, h.ctx).id == action_id
    with pytest.raises(ActionNotFoundError):
        h.service.get(action_id, AuthContext.of(STRANGER))


# --- 拒绝 -----------------------------------------------------------------------


def test_reject_is_terminal_and_writes_no_refund(h: Harness) -> None:
    action_id = propose(h)
    record = h.service.reject(action_id, h.ctx, note="用户改主意了")
    assert record.status is ActionStatus.REJECTED
    assert h.refund_count() == 0
    assert h.events() == ["action_proposed", "action_rejected"]
    assert h.audit()[-1].payload["note"] == "用户改主意了"
    with pytest.raises(ActionStateError):
        h.service.confirm(action_id, h.ctx)


# --- 审计（FR-507）---------------------------------------------------------------


def test_audit_answers_who_when_which_rule_and_what_happened(h: Harness) -> None:
    action_id = propose(h)
    h.service.confirm(action_id, h.ctx)
    executed = h.audit()[-1]

    assert executed.event_type == "action_executed"
    assert (executed.actor_type, executed.actor_id) == ("customer", str(OWNER))  # 谁
    assert executed.ts == T0  # 何时
    assert (executed.policy_id, executed.policy_version) == ("REFUND-STD-001", 3)  # 依据哪条规则
    assert executed.reason_code == ReasonCode.POLICY_SATISFIED.value
    assert executed.payload["status"] == ActionStatus.SUCCEEDED.value  # 结果如何
    assert executed.payload["refund_id"] == h.refunds()[0].id
    assert executed.action_id == action_id


def test_audit_row_is_appended_on_every_state_change(h: Harness) -> None:
    action_id = propose(h)
    assert len(h.audit()) == 1
    h.service.confirm(action_id, h.ctx)
    assert len(h.audit()) == 2
    h.service.confirm(action_id, h.ctx)  # 重放也留痕，但不改动已有行
    assert len(h.audit()) == 3


def test_service_exposes_no_way_to_change_audit_rows(h: Harness) -> None:
    """FR-507：追加式。应用层不提供 UPDATE / DELETE 审计的路径。"""
    forbidden = [
        name
        for name in dir(ActionService)
        if any(word in name.lower() for word in ("update_audit", "delete_audit", "purge", "clear"))
    ]
    assert forbidden == []


# --- 失败与重试（FR-508）----------------------------------------------------------


class FlakyRefundService(RefundService):
    """前 `fail_times` 次执行抛错，用来验证失败路径与重试不产生重复副作用。"""

    calls = 0
    fail_times = 1

    def execute_refund(self, **kwargs: object) -> Refund:  # type: ignore[override]
        type(self).calls += 1
        if type(self).calls <= type(self).fail_times:
            raise RuntimeError("模拟支付通道超时")
        return super().execute_refund(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def flaky(engine: Engine) -> Iterator[Harness]:
    FlakyRefundService.calls = 0
    FlakyRefundService.fail_times = 1
    yield from _make_harness(engine, refund_factory=FlakyRefundService)


def test_failed_execution_is_recorded_and_retry_succeeds_once(flaky: Harness) -> None:
    action_id = propose(flaky)

    with pytest.raises(RuntimeError):
        flaky.service.confirm(action_id, flaky.ctx)
    assert flaky.status(action_id) is ActionStatus.FAILED
    assert flaky.refund_count() == 0
    assert flaky.events() == ["action_proposed", "action_execution_failed"]

    outcome = flaky.service.confirm(action_id, flaky.ctx)  # 重试命中同一幂等键
    assert outcome.replay is False
    assert flaky.status(action_id) is ActionStatus.SUCCEEDED
    assert flaky.refund_count() == 1
    assert flaky.events()[-1] == "action_executed"

    again = flaky.service.confirm(action_id, flaky.ctx)
    assert again.replay is True
    assert flaky.refund_count() == 1
