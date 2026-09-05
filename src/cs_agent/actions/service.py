"""`ActionService`：写操作的落库、确认与幂等执行（FR-502~508）。

事务边界在本层（CLAUDE.md §7）。三条不变式：

1. **防重靠数据库，不靠应用层**。`propose()` 用
   `INSERT … ON CONFLICT (idempotency_key) DO NOTHING` —— 先写、冲突了再回读，
   **不是**"先 SELECT 看有没有再 INSERT"（PRD §7.4 明令禁止后者）。
   一个 `(user, action_type, params, window)` 在库里永远只有一行。
2. **执行串行化靠行锁**。`confirm()` / `execute()` 都以 `SELECT … FOR UPDATE` 开头：
   并发的第二个调用会阻塞到第一个提交，然后读到 `succeeded` 直接返回原结果
   （`replay=True`，FR-503/508），不会产生第二条 `biz.refunds`。
   checkpoint 重放（ADR-0003）走的也是这条路。
3. **审计只增不改**。本类**没有**任何 UPDATE / DELETE `audit_log` 的方法（FR-507）。
   每次状态变化追加一行，含 actor / 时间 / 规则 id 与版本 / 判定 / 结果。

归属与存在性：他人的动作与不存在的动作抛**同一个** `ActionNotFoundError`（FR-505、FR-804），
调用方无从区分，避免通过枚举 action_id 探测别人有没有发起过退款。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from cs_agent.actions.errors import ActionExpiredError, ActionNotFoundError, ActionStateError
from cs_agent.actions.proposal import ActionProposal, ActionType, ParamValue, idempotency_key
from cs_agent.actions.state import (
    EXECUTABLE,
    WAITING,
    ActionEvent,
    ActionStatus,
    transition,
)
from cs_agent.auth.context import AuthContext
from cs_agent.db.models.agent import AgentAction, AuditLog
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.policy.engine import PolicyVerdict
from cs_agent.services.refund import RefundService

#: 等待确认 / 审批的动作默认 24 小时后过期（FR-504）。
DEFAULT_TTL = timedelta(hours=24)

#: 决策终态 → 提议之后立即触发的状态机事件。其余终态不产生动作。
OUTCOME_EVENTS: dict[DecisionOutcome, ActionEvent] = {
    DecisionOutcome.REQUIRE_CONFIRMATION: ActionEvent.REQUIRE_CONFIRMATION,
    DecisionOutcome.REQUIRE_HUMAN: ActionEvent.REQUIRE_HUMAN,
    DecisionOutcome.DENY: ActionEvent.REJECT,
}

ACTOR_CUSTOMER = "customer"
ACTOR_SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """`agent_actions` 一行的只读快照。不把 ORM 实体泄漏给上层，避免上层顺手改字段。"""

    id: int
    thread_id: UUID
    user_id: int
    action_type: ActionType
    params: Mapping[str, Any]
    idempotency_key: str
    status: ActionStatus
    policy_id: str | None
    policy_version: int | None
    reason_code: ReasonCode | None
    result: Mapping[str, Any] | None
    proposed_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """执行结果。`replay=True` 表示这次没有产生新的副作用，返回的是原结果。"""

    action: ActionRecord
    replay: bool

    @property
    def reason_code(self) -> ReasonCode:
        """重放对用户表达为 `IDEMPOTENT_REPLAY`（矩阵规则 11）。"""
        return ReasonCode.IDEMPOTENT_REPLAY if self.replay else ReasonCode.POLICY_SATISFIED


class ActionService:
    """注入 Session 工厂与时钟；两者都可替换，测试不需要等真实时间流逝。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = DEFAULT_TTL,
        refund_factory: Callable[[Session], RefundService] = RefundService,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl
        self._refund_factory = refund_factory

    # --- 提议 -------------------------------------------------------------------

    def propose(
        self,
        ctx: AuthContext,
        thread_id: UUID,
        proposal: ActionProposal,
        *,
        outcome: DecisionOutcome,
        verdict: PolicyVerdict | None = None,
        window_start: datetime | None = None,
    ) -> ActionRecord:
        """把一个 `ActionProposal` 落成 `agent_actions` 一行。

        同一 `(user, action_type, params, window)` 重复提议不会插第二行：
        唯一索引冲突后回读原行返回（图重放、用户连点都走这条路）。
        """
        if outcome not in OUTCOME_EVENTS:
            raise ActionStateError(f"终态 {outcome} 不产生待执行动作")

        now = self._clock()
        window = window_start or now
        key = idempotency_key(ctx.user_id, proposal.action_type, proposal.params, window)
        status = transition(ActionStatus.PROPOSED, OUTCOME_EVENTS[outcome])
        expires_at = now + self._ttl if status in WAITING else None

        with self._session_factory() as session, session.begin():
            stmt = (
                pg_insert(AgentAction)
                .values(
                    thread_id=thread_id,
                    user_id=ctx.user_id,
                    action_type=proposal.action_type.value,
                    params=proposal.as_jsonb(),
                    params_hash=key[:64],
                    idempotency_key=key,
                    status=status.value,
                    policy_id=verdict.policy_id if verdict else None,
                    policy_version=verdict.policy_version if verdict else None,
                    reason_code=verdict.reason_code.value if verdict else None,
                    proposed_at=now,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(AgentAction.id)
            )
            inserted_id = session.execute(stmt).scalar_one_or_none()
            if inserted_id is None:
                # 唯一索引拦下了重复提议：回读原行，不新建、不改写
                action = self._load(session, key=key, user_id=ctx.user_id)
                self._audit(
                    session,
                    action,
                    actor_type=ACTOR_SYSTEM,
                    actor_id=str(ctx.user_id),
                    event_type="action_proposal_replayed",
                    now=now,
                )
                return _record(action)

            action = self._load(session, action_id=inserted_id, user_id=ctx.user_id)
            self._audit(
                session,
                action,
                actor_type=ACTOR_SYSTEM,
                actor_id=str(ctx.user_id),
                event_type="action_proposed",
                now=now,
            )
            return _record(action)

    # --- 确认与执行 --------------------------------------------------------------

    def confirm(
        self, action_id: int, ctx: AuthContext, *, verdict: PolicyVerdict | None = None
    ) -> ExecutionOutcome:
        """用户确认（PRD §5.3 第二段流）：校验归属 → 未过期 → 状态正确 → 幂等执行。"""
        return self._run(action_id, ctx, ActionEvent.CONFIRM, verdict=verdict)

    def execute(
        self, action_id: int, ctx: AuthContext, *, verdict: PolicyVerdict | None = None
    ) -> ExecutionOutcome:
        """人工审批通过后执行，或失败后重试（FR-508）。与 `confirm()` 共用同一条幂等路径。"""
        return self._run(action_id, ctx, ActionEvent.APPROVE, verdict=verdict)

    def reject(self, action_id: int, ctx: AuthContext, *, note: str | None = None) -> ActionRecord:
        """用户或人工拒绝。终态，不产生副作用。"""
        now = self._clock()
        expired_at: datetime | None = None
        record: ActionRecord | None = None
        with self._session_factory() as session, session.begin():
            action = self._locked(session, action_id, ctx.user_id)
            expired_at = self._expire_if_due(session, action, now)
            if expired_at is None:
                action.status = transition(ActionStatus(action.status), ActionEvent.REJECT).value
                action.decided_at = now
                self._audit(
                    session,
                    action,
                    actor_type=ACTOR_CUSTOMER,
                    actor_id=str(ctx.user_id),
                    event_type="action_rejected",
                    now=now,
                    extra={"note": note},
                )
                record = _record(action)
        if expired_at is not None:
            raise ActionExpiredError(f"动作 {action_id} 已于 {expired_at.isoformat()} 过期")
        assert record is not None
        return record

    def _run(
        self,
        action_id: int,
        ctx: AuthContext,
        event: ActionEvent,
        *,
        verdict: PolicyVerdict | None,
    ) -> ExecutionOutcome:
        now = self._clock()
        try:
            expired_at: datetime | None = None
            outcome: ExecutionOutcome | None = None
            with self._session_factory() as session, session.begin():
                action = self._locked(session, action_id, ctx.user_id)

                if action.status == ActionStatus.SUCCEEDED.value:
                    # 已经成功过：返回原结果，不再触碰业务库（FR-503）
                    self._audit(
                        session,
                        action,
                        actor_type=ACTOR_CUSTOMER,
                        actor_id=str(ctx.user_id),
                        event_type="action_execution_replayed",
                        now=now,
                    )
                    outcome = ExecutionOutcome(action=_record(action), replay=True)
                else:
                    expired_at = self._expire_if_due(session, action, now)
                    if expired_at is None:
                        current = ActionStatus(action.status)
                        if current not in EXECUTABLE:
                            raise ActionStateError(
                                f"动作 {action_id} 当前状态 {current} 不接受 {event}"
                            )
                        claim = ActionEvent.RETRY if current is ActionStatus.FAILED else event
                        action.status = transition(current, claim).value
                        action.decided_at = now

                        result = self._perform(session, action, ctx, verdict, now)
                        action.status = transition(
                            ActionStatus.EXECUTING, ActionEvent.SUCCEED
                        ).value
                        action.executed_at = now
                        action.result = result
                        self._audit(
                            session,
                            action,
                            actor_type=ACTOR_CUSTOMER,
                            actor_id=str(ctx.user_id),
                            event_type="action_executed",
                            now=now,
                            extra=result,
                        )
                        outcome = ExecutionOutcome(action=_record(action), replay=False)

            # 过期的状态与审计已经随上面的事务提交，现在才抛（见 _expire_if_due）
            if expired_at is not None:
                raise ActionExpiredError(f"动作 {action_id} 已于 {expired_at.isoformat()} 过期")
            assert outcome is not None
            return outcome
        except (ActionNotFoundError, ActionExpiredError, ActionStateError):
            raise
        except Exception as exc:
            # 执行本身失败：上面的事务已回滚，另起一个事务把 failed 与审计落下来，
            # 保留 idempotency_key 不变，重试仍命中同一行（FR-508）
            self._mark_failed(action_id, ctx, exc, now)
            raise

    def _perform(
        self,
        session: Session,
        action: AgentAction,
        ctx: AuthContext,
        verdict: PolicyVerdict | None,
        now: datetime,
    ) -> dict[str, Any]:
        """把动作交给对应的业务服务。本方法不做资格判断，只做分派。"""
        action_type = ActionType(action.action_type)
        if action_type is not ActionType.REFUND:
            raise ActionStateError(f"本阶段尚未实现 {action_type} 的执行")

        effective = verdict or _verdict_from_row(action)
        params: Mapping[str, Any] = action.params
        refund = self._refund_factory(session).execute_refund(
            order_id=int(params["order_id"]),
            # 归属来自 AuthContext，不来自 params（红线 1）
            user_id=ctx.user_id,
            # 与 Numeric(12,2) 对齐：写库与回填结果都用两位定点，避免 "89" / "89.00" 两种写法
            amount=Decimal(str(params["amount"])).quantize(Decimal("0.01")),
            verdict=effective,
            now=now,
        )
        return {
            "refund_id": refund.id,
            "amount": format(refund.amount, "f"),
            "status": refund.status,
            "simulated": refund.simulated,
        }

    def _mark_failed(
        self, action_id: int, ctx: AuthContext, exc: BaseException, now: datetime
    ) -> None:
        with self._session_factory() as session, session.begin():
            action = self._locked(session, action_id, ctx.user_id)
            if ActionStatus(action.status) is not ActionStatus.EXECUTING:
                action.status = ActionStatus.EXECUTING.value
            action.status = transition(ActionStatus.EXECUTING, ActionEvent.FAIL).value
            self._audit(
                session,
                action,
                actor_type=ACTOR_SYSTEM,
                actor_id=str(ctx.user_id),
                event_type="action_execution_failed",
                now=now,
                extra={"error": exc.__class__.__name__},
            )

    # --- 查询与校验 --------------------------------------------------------------

    def get(self, action_id: int, ctx: AuthContext) -> ActionRecord:
        """按归属读取。他人的与不存在的都抛 `ActionNotFoundError`。"""
        with self._session_factory() as session:
            return _record(self._load(session, action_id=action_id, user_id=ctx.user_id))

    def _locked(self, session: Session, action_id: int, user_id: int) -> AgentAction:
        """取行并加行锁：并发的第二个执行会在这里等，等到的是已提交的最终状态。"""
        stmt = (
            select(AgentAction)
            .where(AgentAction.id == action_id, AgentAction.user_id == user_id)
            .with_for_update()
        )
        action = session.scalars(stmt).one_or_none()
        if action is None:
            raise ActionNotFoundError(f"动作 {action_id} 不存在")
        return action

    def _load(
        self,
        session: Session,
        *,
        action_id: int | None = None,
        key: str | None = None,
        user_id: int,
    ) -> AgentAction:
        stmt = select(AgentAction).where(AgentAction.user_id == user_id)
        if action_id is not None:
            stmt = stmt.where(AgentAction.id == action_id)
        if key is not None:
            stmt = stmt.where(AgentAction.idempotency_key == key)
        action = session.scalars(stmt).one_or_none()
        if action is None:
            raise ActionNotFoundError("动作不存在")
        return action

    def _expire_if_due(
        self, session: Session, action: AgentAction, now: datetime
    ) -> datetime | None:
        """到期的等待态落 `expired` 并追加审计，返回过期时刻；未到期返回 None。

        **不在这里抛异常**：抛在事务里会把刚写的状态和审计一起回滚掉，
        用户看到 410，库里却还是 `awaiting_confirmation`。调用方提交之后再抛（FR-504）。
        """
        status = ActionStatus(action.status)
        if status not in WAITING or action.expires_at is None:
            return None
        expires_at = _aware(action.expires_at)
        if expires_at > now:
            return None
        action.status = transition(status, ActionEvent.EXPIRE).value
        self._audit(
            session,
            action,
            actor_type=ACTOR_SYSTEM,
            actor_id=None,
            event_type="action_expired",
            now=now,
            extra={"expires_at": expires_at.isoformat()},
        )
        return expires_at

    # --- 审计（只增，没有 update / delete 的对应方法，FR-507）-----------------------

    def _audit(
        self,
        session: Session,
        action: AgentAction,
        *,
        actor_type: str,
        actor_id: str | None,
        event_type: str,
        now: datetime,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "action_type": action.action_type,
            "status": action.status,
            "params": action.params,
            "idempotency_key": action.idempotency_key,
        }
        if extra:
            payload.update(extra)
        session.add(
            AuditLog(
                ts=now,
                actor_type=actor_type,
                actor_id=actor_id,
                thread_id=action.thread_id,
                action_id=action.id,
                event_type=event_type,
                policy_id=action.policy_id,
                policy_version=action.policy_version,
                reason_code=action.reason_code,
                payload=payload,
            )
        )


def _record(action: AgentAction) -> ActionRecord:
    return ActionRecord(
        id=action.id,
        thread_id=action.thread_id,
        user_id=action.user_id,
        action_type=ActionType(action.action_type),
        params=dict(action.params),
        idempotency_key=action.idempotency_key,
        status=ActionStatus(action.status),
        policy_id=action.policy_id,
        policy_version=action.policy_version,
        reason_code=ReasonCode(action.reason_code) if action.reason_code else None,
        result=dict(action.result) if action.result else None,
        proposed_at=_aware(action.proposed_at),
        expires_at=_aware(action.expires_at) if action.expires_at else None,
    )


def _verdict_from_row(action: AgentAction) -> PolicyVerdict:
    """执行时若没另外传判定，就用提议时记下的那一份——引用与执行同源（ADR-0006）。"""
    from cs_agent.policy.engine import PolicyOutcome

    return PolicyVerdict(
        outcome=PolicyOutcome.ALLOW,
        reason_code=ReasonCode(action.reason_code) if action.reason_code else ReasonCode.OK,
        policy_id=action.policy_id,
        policy_version=action.policy_version,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = [
    "ACTOR_CUSTOMER",
    "ACTOR_SYSTEM",
    "DEFAULT_TTL",
    "ActionRecord",
    "ActionService",
    "ExecutionOutcome",
    "ParamValue",
]
