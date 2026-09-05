"""人工审批内核（PRD §5.4，FR-603~606）。

三条处置路径都落在这里：

```
awaiting_human ──approve──▶ ActionService.execute  ──▶ succeeded
               ──edit─────▶ 改 params + 重算幂等键 ──▶ ActionService.execute
               ──reject───▶ ActionService.reject    ──▶ rejected
```

**执行仍然只有一条路**：本服务不自己写 `biz.refunds`，一律委托 `ActionService`，
因此幂等键与行锁那套防重（FR-503/508）对人工路径同样生效——审批人连点两次
approve，第二次拿到的是 `replay=True` 的原结果，不会退第二笔。

`edit` 的语义（FR-605）：改了参数就是**另一笔动作**，必须重算幂等键，否则新参数会
命中旧键、把上一版的执行结果当成"已办理"返回。窗口取动作的 `proposed_at`，
保证同一笔动作反复编辑到同样的参数时算出同样的键。

审计（FR-606）：每一步都追加一行 `actor_type="human"`，带审批人与备注。
与 `ActionService` 自己写的执行行并存——"谁批的"看本服务的行，"执行结果如何"看那一行。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cs_agent.actions import (
    ActionProposal,
    ActionService,
    ActionStatus,
    ActionType,
    ExecutionOutcome,
    idempotency_key,
)
from cs_agent.actions.proposal import ParamValue
from cs_agent.actions.state import ActionEvent, transition
from cs_agent.auth.context import AuthContext
from cs_agent.db.models.agent import AgentAction, AuditLog, HumanReview
from cs_agent.domain.enums import ReasonCode

#: 审计里的 actor 类型。人工处置一律记 human（FR-606）。
ACTOR_HUMAN = "human"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    """处置方式。`EDIT` 也算通过，但要单独留痕——参数被改过是审计的关键信息。"""

    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class HumanReviewError(Exception):
    """人工审批层异常基类。"""


class ReviewNotFoundError(HumanReviewError):
    """审批单不存在。"""


class ReviewClosedError(HumanReviewError):
    """审批单已被处置过。二次处置要报错，不能静默覆盖前一个人的决定。"""


@dataclass(frozen=True, slots=True)
class HumanReviewRecord:
    """`human_reviews` 一行的只读快照。"""

    id: int
    action_id: int
    thread_id: UUID
    reason_code: ReasonCode
    status: ReviewStatus
    assigned_to: str | None
    decision: ReviewDecision | None
    note: str | None
    edited_params: Mapping[str, Any] | None
    created_at: datetime
    decided_at: datetime | None


class HumanReviewService:
    """注入 Session 工厂、时钟与 `ActionService`。三者都可替换，测试不依赖真实时间。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        actions: ActionService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._actions = actions
        self._clock = clock or (lambda: datetime.now(UTC))

    # --- 入队与列表（FR-603）---------------------------------------------------

    def enqueue(self, action_id: int, reason_code: ReasonCode) -> HumanReviewRecord:
        """把一个 `awaiting_human` 的动作放进审批队列。

        同一动作已有待审单时直接返回原单，不排第二次——图重放（ADR-0003）会重复调用。
        注意这只是**去重**，不是防重：真正防重复退款的仍是 `agent_actions` 的唯一键，
        `human_reviews` 上没有唯一索引，也不该靠它兜住金钱副作用。
        """
        now = self._clock()
        with self._session_factory() as session, session.begin():
            action = self._action(session, action_id)
            if ActionStatus(action.status) is not ActionStatus.AWAITING_HUMAN:
                raise ReviewClosedError(
                    f"动作 {action_id} 当前状态 {action.status}，不需要人工审批"
                )
            existing = session.scalars(
                select(HumanReview).where(
                    HumanReview.action_id == action_id,
                    HumanReview.status == ReviewStatus.PENDING.value,
                )
            ).first()
            if existing is not None:
                return _record(existing)

            review = HumanReview(
                action_id=action_id,
                thread_id=action.thread_id,
                reason_code=reason_code.value,
                status=ReviewStatus.PENDING.value,
                created_at=now,
            )
            session.add(review)
            session.flush()
            self._audit(
                session,
                action,
                review,
                operator=None,
                event_type="review_enqueued",
                now=now,
            )
            return _record(review)

    def list_pending(self, *, limit: int = 50) -> list[HumanReviewRecord]:
        """待审列表，先进先出。

        **刻意不按用户 scope 过滤**——审批人本来就要看别人的单子。正因如此，
        调用它的接口必须挡在人工坐席角色之后（FR-603），不能暴露给普通客户。
        """
        with self._session_factory() as session:
            rows = session.scalars(
                select(HumanReview)
                .where(HumanReview.status == ReviewStatus.PENDING.value)
                .order_by(HumanReview.created_at, HumanReview.id)
                .limit(limit)
            )
            return [_record(r) for r in rows]

    def get(self, review_id: int) -> HumanReviewRecord:
        with self._session_factory() as session:
            return _record(self._review(session, review_id))

    # --- 三种处置（FR-604）-----------------------------------------------------

    def approve(
        self, review_id: int, operator: str, *, note: str | None = None
    ) -> ExecutionOutcome:
        """原样批准并执行。执行委托 `ActionService`，幂等保证不变。"""
        now = self._clock()
        action_id, owner = self._close(
            review_id,
            operator,
            ReviewDecision.APPROVE,
            note=note,
            event_type="review_approved",
            now=now,
        )
        return self._actions.execute(action_id, AuthContext.of(owner))

    def edit(
        self,
        review_id: int,
        operator: str,
        edited_params: Mapping[str, ParamValue],
        *,
        note: str | None = None,
    ) -> ExecutionOutcome:
        """改参数后执行（FR-605）。参数变了 → 幂等键必须变，否则会命中上一版的结果。

        这里**不重跑策略引擎**：金额超限走人工审批本来就是 §9.4 规则 10 的出口，
        人工判断就是这条路的授权来源。代价是审批人可以把金额改成任意值——
        这靠操作员角色 + 审计追责兜住，不靠再加一道自动校验（那会变成两套规则）。
        """
        now = self._clock()
        with self._session_factory() as session, session.begin():
            review = self._pending(session, review_id)
            action = self._action(session, review.action_id)
            owner = action.user_id

            # 复用提议的参数校验：必填项齐全、不许夹带身份字段
            proposal = ActionProposal(ActionType(action.action_type), edited_params)
            # 状态机上的自环：确认 awaiting_human 才允许编辑
            action.status = transition(ActionStatus(action.status), ActionEvent.EDIT).value

            new_key = idempotency_key(
                owner,
                proposal.action_type,
                proposal.params,
                _aware(action.proposed_at),
            )
            action.params = proposal.as_jsonb()
            action.params_hash = new_key[:64]
            action.idempotency_key = new_key

            self._resolve(review, operator, ReviewDecision.EDIT, note, now)
            review.edited_params = proposal.as_jsonb()
            self._audit(
                session,
                action,
                review,
                operator=operator,
                event_type="review_edited",
                now=now,
                extra={"edited_params": review.edited_params, "note": note},
            )
            action_id = action.id

        return self._actions.execute(action_id, AuthContext.of(owner))

    def reject(self, review_id: int, operator: str, note: str | None = None) -> HumanReviewRecord:
        """驳回。动作进 `rejected` 终态，不产生任何业务副作用。"""
        now = self._clock()
        action_id, owner = self._close(
            review_id,
            operator,
            ReviewDecision.REJECT,
            note=note,
            event_type="review_rejected",
            now=now,
        )
        self._actions.reject(action_id, AuthContext.of(owner), note=note)
        with self._session_factory() as session:
            return _record(self._review(session, review_id))

    # --- 内部 -------------------------------------------------------------------

    def _close(
        self,
        review_id: int,
        operator: str,
        decision: ReviewDecision,
        *,
        note: str | None,
        event_type: str,
        now: datetime,
    ) -> tuple[int, int]:
        """把审批单结掉并留痕，返回 `(action_id, 动作归属用户)`。"""
        with self._session_factory() as session, session.begin():
            review = self._pending(session, review_id)
            action = self._action(session, review.action_id)
            self._resolve(review, operator, decision, note, now)
            self._audit(
                session,
                action,
                review,
                operator=operator,
                event_type=event_type,
                now=now,
                extra={"note": note},
            )
            return action.id, action.user_id

    @staticmethod
    def _resolve(
        review: HumanReview,
        operator: str,
        decision: ReviewDecision,
        note: str | None,
        now: datetime,
    ) -> None:
        review.status = (
            ReviewStatus.REJECTED.value
            if decision is ReviewDecision.REJECT
            else ReviewStatus.APPROVED.value
        )
        review.decision = decision.value
        review.assigned_to = operator
        review.note = note
        review.decided_at = now

    def _pending(self, session: Session, review_id: int) -> HumanReview:
        """取待审单并加行锁：两个审批人同时点，第二个会看到已处置而不是覆盖前一个。"""
        review = session.scalars(
            select(HumanReview).where(HumanReview.id == review_id).with_for_update()
        ).one_or_none()
        if review is None:
            raise ReviewNotFoundError(f"审批单 {review_id} 不存在")
        if ReviewStatus(review.status) is not ReviewStatus.PENDING:
            raise ReviewClosedError(
                f"审批单 {review_id} 已由 {review.assigned_to} 处置为 {review.decision}"
            )
        return review

    @staticmethod
    def _review(session: Session, review_id: int) -> HumanReview:
        review = session.get(HumanReview, review_id)
        if review is None:
            raise ReviewNotFoundError(f"审批单 {review_id} 不存在")
        return review

    @staticmethod
    def _action(session: Session, action_id: int) -> AgentAction:
        action = session.get(AgentAction, action_id, with_for_update=True)
        if action is None:
            raise ReviewNotFoundError(f"动作 {action_id} 不存在")
        return action

    # --- 审计：只增，本类同样没有改 / 删审计的方法（FR-507）----------------------

    @staticmethod
    def _audit(
        session: Session,
        action: AgentAction,
        review: HumanReview,
        *,
        operator: str | None,
        event_type: str,
        now: datetime,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "review_id": review.id,
            "review_status": review.status,
            "decision": review.decision,
            "action_type": action.action_type,
            "action_status": action.status,
            "idempotency_key": action.idempotency_key,
        }
        if extra:
            payload.update({k: v for k, v in extra.items() if v is not None})
        session.add(
            AuditLog(
                ts=now,
                actor_type=ACTOR_HUMAN,
                actor_id=operator,
                thread_id=action.thread_id,
                action_id=action.id,
                event_type=event_type,
                policy_id=action.policy_id,
                policy_version=action.policy_version,
                reason_code=review.reason_code,
                payload=payload,
            )
        )


def _record(review: HumanReview) -> HumanReviewRecord:
    return HumanReviewRecord(
        id=review.id,
        action_id=review.action_id,
        thread_id=review.thread_id,
        reason_code=ReasonCode(review.reason_code),
        status=ReviewStatus(review.status),
        assigned_to=review.assigned_to,
        decision=ReviewDecision(review.decision) if review.decision else None,
        note=review.note,
        edited_params=dict(review.edited_params) if review.edited_params else None,
        created_at=_aware(review.created_at),
        decided_at=_aware(review.decided_at) if review.decided_at else None,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = [
    "ACTOR_HUMAN",
    "HumanReviewError",
    "HumanReviewRecord",
    "HumanReviewService",
    "ReviewClosedError",
    "ReviewDecision",
    "ReviewNotFoundError",
    "ReviewStatus",
]
