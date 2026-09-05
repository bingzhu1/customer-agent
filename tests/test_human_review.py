"""人工审批内核（FR-603~606）。

连 `.env` 的 DATABASE_URL（本 worktree 为 cs_agent_p3）；不可达时 skip 整个文件。
用例自建自清：每个用例一个新 thread，结束时删掉自己写的行，不跑 seed。
"""

from __future__ import annotations

from collections.abc import Iterator
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
    ActionProposal,
    ActionService,
    ActionStatus,
    ActionType,
    InvalidProposalError,
    idempotency_key,
)
from cs_agent.auth.context import AuthContext
from cs_agent.db.models.agent import AgentAction, AuditLog, HumanReview, Thread
from cs_agent.db.models.biz import Refund
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.policy.engine import PolicyOutcome, PolicyVerdict
from cs_agent.services.human_review import (
    ACTOR_HUMAN,
    HumanReviewService,
    ReviewClosedError,
    ReviewDecision,
    ReviewNotFoundError,
    ReviewStatus,
)
from cs_agent.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
#: 契约 §2 的 82918：620.00 > max_auto_amount 200 → 矩阵规则 10 → 人工审批，正是本文件的场景
TEST_ORDER = 82918
OWNER = 101
OPERATOR = "supervisor-7"
AMOUNT = Decimal("620.00")
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
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass
class Harness:
    factory: sessionmaker[Session]
    actions: ActionService
    reviews: HumanReviewService
    clock: Clock
    thread_id: UUID
    ctx: AuthContext

    def propose_for_human(self) -> int:
        return self.actions.propose(
            self.ctx,
            self.thread_id,
            PROPOSAL,
            outcome=DecisionOutcome.REQUIRE_HUMAN,
            verdict=ALLOW,
            window_start=T0,
        ).id

    def enqueue(self) -> tuple[int, int]:
        action_id = self.propose_for_human()
        review = self.reviews.enqueue(action_id, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT)
        return review.id, action_id

    def refunds(self) -> list[Refund]:
        with self.factory() as s:
            return list(s.scalars(select(Refund).where(Refund.order_id == TEST_ORDER)))

    def refund_count(self) -> int:
        return len(self.refunds())

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

    def human_events(self) -> list[AuditLog]:
        return [row for row in self.audit() if row.actor_type == ACTOR_HUMAN]

    def action(self, action_id: int) -> AgentAction:
        with self.factory() as s:
            row = s.get(AgentAction, action_id)
            assert row is not None
            return row

    def review_count(self) -> int:
        with self.factory() as s:
            return int(
                s.scalar(
                    select(func.count())
                    .select_from(HumanReview)
                    .where(HumanReview.thread_id == self.thread_id)
                )
                or 0
            )


@pytest.fixture
def h(engine: Engine) -> Iterator[Harness]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    thread_id = uuid4()
    with factory() as s, s.begin():
        s.execute(delete(Refund).where(Refund.order_id == TEST_ORDER))
        s.add(Thread(id=thread_id, user_id=OWNER, status="open", created_at=T0, last_active_at=T0))
    clock = Clock(T0)
    actions = ActionService(factory, clock=clock)
    yield Harness(
        factory,
        actions,
        HumanReviewService(factory, actions, clock=clock),
        clock,
        thread_id,
        AuthContext.of(OWNER),
    )
    with factory() as s, s.begin():
        s.execute(delete(AuditLog).where(AuditLog.thread_id == thread_id))
        s.execute(delete(HumanReview).where(HumanReview.thread_id == thread_id))
        s.execute(delete(AgentAction).where(AgentAction.thread_id == thread_id))
        s.execute(delete(Thread).where(Thread.id == thread_id))
        s.execute(delete(Refund).where(Refund.order_id == TEST_ORDER))


# --- 入队与列表（FR-603）---------------------------------------------------------


def test_enqueue_writes_a_pending_review(h: Harness) -> None:
    action_id = h.propose_for_human()
    review = h.reviews.enqueue(action_id, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT)

    assert review.status is ReviewStatus.PENDING
    assert review.action_id == action_id
    assert review.reason_code is ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT
    assert (review.assigned_to, review.decision, review.decided_at) == (None, None, None)
    assert h.events() == ["action_proposed", "review_enqueued"]


def test_enqueue_is_idempotent_for_the_same_action(h: Harness) -> None:
    """图重放会重复调用，队列里不该出现两张单子。"""
    action_id = h.propose_for_human()
    first = h.reviews.enqueue(action_id, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT)
    second = h.reviews.enqueue(action_id, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT)
    assert first.id == second.id
    assert h.review_count() == 1


def test_enqueue_refuses_an_action_that_does_not_need_review(h: Harness) -> None:
    record = h.actions.propose(
        h.ctx,
        h.thread_id,
        PROPOSAL,
        outcome=DecisionOutcome.REQUIRE_CONFIRMATION,
        verdict=ALLOW,
    )
    with pytest.raises(ReviewClosedError):
        h.reviews.enqueue(record.id, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT)


def test_list_pending_is_fifo_and_drops_resolved_reviews(h: Harness) -> None:
    first_id, _ = h.enqueue()
    h.clock.advance(timedelta(minutes=1))
    second = h.actions.propose(
        h.ctx,
        h.thread_id,
        ActionProposal(
            ActionType.REFUND,
            {"order_id": TEST_ORDER, "amount": Decimal("300.00"), "reason": "另一笔"},
        ),
        outcome=DecisionOutcome.REQUIRE_HUMAN,
        verdict=ALLOW,
    )
    second_id = h.reviews.enqueue(second.id, ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT).id

    pending = [r.id for r in h.reviews.list_pending()]
    assert pending.index(first_id) < pending.index(second_id)

    h.reviews.reject(first_id, OPERATOR, "不予受理")
    assert first_id not in [r.id for r in h.reviews.list_pending()]
    assert second_id in [r.id for r in h.reviews.list_pending()]


def test_missing_review_raises(h: Harness) -> None:
    with pytest.raises(ReviewNotFoundError):
        h.reviews.get(10_000_000)
    with pytest.raises(ReviewNotFoundError):
        h.reviews.approve(10_000_000, OPERATOR)


# --- approve（FR-604）------------------------------------------------------------


def test_approve_executes_the_action_once(h: Harness) -> None:
    review_id, action_id = h.enqueue()
    outcome = h.reviews.approve(review_id, OPERATOR, note="核对无误")

    assert outcome.replay is False
    assert outcome.action.status is ActionStatus.SUCCEEDED
    assert h.refund_count() == 1
    assert h.refunds()[0].amount == AMOUNT

    review = h.reviews.get(review_id)
    assert review.status is ReviewStatus.APPROVED
    assert review.decision is ReviewDecision.APPROVE
    assert (review.assigned_to, review.note) == (OPERATOR, "核对无误")
    assert review.decided_at == T0
    assert h.action(action_id).status == ActionStatus.SUCCEEDED.value


def test_approving_twice_is_refused_not_silently_repeated(h: Harness) -> None:
    """第二个审批人点下去要报错，不能静默覆盖前一个人的决定。"""
    review_id, _ = h.enqueue()
    h.reviews.approve(review_id, OPERATOR)
    with pytest.raises(ReviewClosedError, match=OPERATOR):
        h.reviews.approve(review_id, "supervisor-9")
    assert h.refund_count() == 1


def test_human_path_keeps_the_idempotency_guarantee(h: Harness) -> None:
    """人工路径不绕过 ActionService，所以幂等仍然生效：再执行一次是 replay。"""
    review_id, action_id = h.enqueue()
    h.reviews.approve(review_id, OPERATOR)
    again = h.actions.execute(action_id, h.ctx)
    assert again.replay is True
    assert h.refund_count() == 1


# --- edit（FR-605）---------------------------------------------------------------


EDITED = {"order_id": TEST_ORDER, "amount": Decimal("500.00"), "reason": "按协商金额退"}


def test_edit_recomputes_the_idempotency_key(h: Harness) -> None:
    review_id, action_id = h.enqueue()
    before = h.action(action_id)
    old_key, old_hash = before.idempotency_key, before.params_hash

    h.reviews.edit(review_id, OPERATOR, EDITED, note="与用户协商后减半")

    after = h.action(action_id)
    assert after.idempotency_key != old_key
    assert after.params_hash != old_hash
    # 键可复算：窗口取动作的 proposed_at，同样的参数永远算出同样的键
    assert after.idempotency_key == idempotency_key(OWNER, ActionType.REFUND, EDITED, T0)
    assert after.params == {"order_id": TEST_ORDER, "amount": "500", "reason": "按协商金额退"}


def test_edit_executes_with_the_new_amount(h: Harness) -> None:
    review_id, _ = h.enqueue()
    outcome = h.reviews.edit(review_id, OPERATOR, EDITED)

    assert outcome.replay is False
    assert h.refund_count() == 1
    assert h.refunds()[0].amount == Decimal("500.00")  # 不是原来的 620
    assert outcome.action.result is not None
    assert outcome.action.result["amount"] == "500.00"


def test_edit_records_the_operator_and_the_edited_params(h: Harness) -> None:
    review_id, _ = h.enqueue()
    h.reviews.edit(review_id, OPERATOR, EDITED, note="与用户协商后减半")

    review = h.reviews.get(review_id)
    assert review.decision is ReviewDecision.EDIT
    assert review.status is ReviewStatus.APPROVED
    assert review.assigned_to == OPERATOR
    assert review.edited_params == {
        "order_id": TEST_ORDER,
        "amount": "500",
        "reason": "按协商金额退",
    }
    edited_row = [r for r in h.audit() if r.event_type == "review_edited"][0]
    assert edited_row.payload["edited_params"] == review.edited_params
    assert edited_row.payload["note"] == "与用户协商后减半"


def test_edit_rejects_params_that_would_not_pass_proposal_validation(h: Harness) -> None:
    """改参数复用提议那套校验：缺必填项、夹带身份字段都不行（红线 1）。"""
    review_id, _ = h.enqueue()
    with pytest.raises(InvalidProposalError):
        h.reviews.edit(review_id, OPERATOR, {"order_id": TEST_ORDER})
    with pytest.raises(InvalidProposalError):
        h.reviews.edit(
            review_id,
            OPERATOR,
            {**EDITED, "user_id": 202},  # type: ignore[dict-item]
        )
    assert h.reviews.get(review_id).status is ReviewStatus.PENDING
    assert h.refund_count() == 0


def test_edit_then_approve_is_refused(h: Harness) -> None:
    review_id, _ = h.enqueue()
    h.reviews.edit(review_id, OPERATOR, EDITED)
    with pytest.raises(ReviewClosedError):
        h.reviews.approve(review_id, OPERATOR)
    assert h.refund_count() == 1


# --- reject（FR-604）-------------------------------------------------------------


def test_reject_closes_the_action_without_side_effects(h: Harness) -> None:
    review_id, action_id = h.enqueue()
    review = h.reviews.reject(review_id, OPERATOR, "金额与订单不符")

    assert review.status is ReviewStatus.REJECTED
    assert review.decision is ReviewDecision.REJECT
    assert review.note == "金额与订单不符"
    assert h.action(action_id).status == ActionStatus.REJECTED.value
    assert h.refund_count() == 0


# --- 审计（FR-606）---------------------------------------------------------------


def test_every_human_step_is_audited_as_human(h: Harness) -> None:
    review_id, action_id = h.enqueue()
    h.reviews.approve(review_id, OPERATOR, note="核对无误")

    human = h.human_events()
    assert [row.event_type for row in human] == ["review_enqueued", "review_approved"]
    approved = human[-1]
    assert approved.actor_type == ACTOR_HUMAN  # FR-606
    assert approved.actor_id == OPERATOR  # 谁批的
    assert approved.ts == T0  # 何时
    assert (approved.policy_id, approved.policy_version) == ("REFUND-STD-001", 3)  # 依据哪条
    assert approved.reason_code == ReasonCode.AMOUNT_ABOVE_AUTO_LIMIT.value
    assert approved.payload["note"] == "核对无误"
    assert approved.action_id == action_id
    # 执行结果由 ActionService 那一行回答，两行合起来才是完整链路
    assert "action_executed" in h.events()


@pytest.mark.parametrize(
    ("act", "expected"),
    [
        ("approve", "review_approved"),
        ("edit", "review_edited"),
        ("reject", "review_rejected"),
    ],
)
def test_each_disposition_writes_exactly_one_human_row(h: Harness, act: str, expected: str) -> None:
    review_id, _ = h.enqueue()
    if act == "approve":
        h.reviews.approve(review_id, OPERATOR)
    elif act == "edit":
        h.reviews.edit(review_id, OPERATOR, EDITED)
    else:
        h.reviews.reject(review_id, OPERATOR, "不予受理")

    types = [row.event_type for row in h.human_events()]
    assert types == ["review_enqueued", expected]
    assert all(row.actor_type == ACTOR_HUMAN for row in h.human_events())


def test_service_exposes_no_way_to_change_audit_rows() -> None:
    """FR-507：追加式。人工审批这条路同样不提供改 / 删审计的方法。"""
    forbidden = [
        name
        for name in dir(HumanReviewService)
        if any(w in name.lower() for w in ("update_audit", "delete_audit", "purge", "clear"))
    ]
    assert forbidden == []
