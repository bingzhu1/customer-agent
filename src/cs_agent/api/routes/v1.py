"""`/v1` 业务路由（FR-101/102/104/108，PRD §8.1）。

薄路由：只做参数校验、取身份、调 Service、序列化。业务逻辑在 `services/chat.py`，
数据访问在 `repositories/`，这里一行 SQL 都没有（CLAUDE.md §7）。

三个 404 的共同口径：Service 返回 `None` 就是 404，**不区分**"会话不存在"
与"会话不属于你"——避免通过枚举 thread_id 探测存在性（PRD §8.4）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from cs_agent.actions import (
    ActionExpiredError as ServiceActionExpired,
)
from cs_agent.actions import (
    ActionNotFoundError,
    ActionStateError,
)
from cs_agent.api.deps import AuthDep, SessionDep
from cs_agent.api.errors import (
    ActionExpiredError,
    ActionStateConflictError,
    NotFoundError,
)
from cs_agent.api.schemas import (
    CitationOut,
    ConfirmActionRequest,
    ConfirmActionResponse,
    CreateThreadResponse,
    DevTokenRequest,
    DevTokenResponse,
    MessageOut,
    MessageResponse,
    PendingActionOut,
    PendingActionSummary,
    SendMessageRequest,
    ThreadDetailResponse,
    UsageOut,
)
from cs_agent.auth.context import Role
from cs_agent.auth.jwt import issue_token
from cs_agent.domain.enums import ReasonCode
from cs_agent.eval.pricing import estimate_cost_usd
from cs_agent.services.chat import ChatService, TurnOutcome
from cs_agent.settings import get_settings

router = APIRouter(prefix="/v1")


@router.get("/whoami", tags=["auth"])
def whoami(auth: AuthDep) -> dict[str, object]:
    """回显当前身份。注意：值来自 token 校验结果，与请求体无关。"""
    return {"user_id": auth.user_id, "roles": sorted(r.value for r in auth.roles)}


@router.post("/threads", status_code=status.HTTP_201_CREATED, tags=["threads"])
def create_thread(auth: AuthDep, session: SessionDep) -> CreateThreadResponse:
    """FR-101：创建会话。`thread_id` 与调用方身份绑定，请求体里给不了别人的身份。"""
    thread = ChatService(session, auth).create_thread()
    return CreateThreadResponse(
        thread_id=thread.id, status=thread.status, created_at=thread.created_at
    )


@router.post("/threads/{thread_id}/messages", tags=["threads"])
def send_message(
    thread_id: UUID,
    payload: SendMessageRequest,
    auth: AuthDep,
    session: SessionDep,
    request: Request,
) -> MessageResponse:
    """FR-102：发送消息并拿到完整响应（非流式）。SSE 版本是 FR-103，Phase 4。"""
    outcome = ChatService(session, auth).send_message(thread_id, payload.message)
    if outcome is None:
        raise NotFoundError("会话不存在")
    return _to_message_response(outcome, getattr(request.state, "request_id", None))


@router.get("/threads/{thread_id}", tags=["threads"])
def get_thread(thread_id: UUID, auth: AuthDep, session: SessionDep) -> ThreadDetailResponse:
    """FR-104：只返回本人会话；他人会话与不存在的会话都是 404。"""
    view = ChatService(session, auth).get_thread_view(thread_id)
    if view is None:
        raise NotFoundError("会话不存在")
    return ThreadDetailResponse(
        thread_id=view.thread.id,
        status=view.thread.status,
        created_at=view.thread.created_at,
        last_active_at=view.thread.last_active_at,
        messages=[
            MessageOut(role=m.role, content=m.content, created_at=m.created_at)
            for m in view.messages
        ],
        case_facts=view.case_facts.to_json_dict(),
        narrative_summary=view.narrative_summary,
    )


@router.post("/actions/{action_id}/confirm", tags=["actions"])
def confirm_action(
    action_id: int,
    payload: ConfirmActionRequest,
    auth: AuthDep,
    session: SessionDep,
    request: Request,
) -> ConfirmActionResponse:
    """FR-602/503：用户确认后幂等执行；`confirm=false` 则放弃该动作。

    三种失败对外的口径（PRD §8.4）：
    - 动作不存在**或**不属于当前用户 → 404，信封完全一致，不泄露存在性（FR-505）；
    - 已过 `expires_at` → 410；
    - 状态不接受确认（已驳回 / 已过期）→ 409。

    重复确认不是错误：第二次返回 `replay=true` 与上一次相同的结果，绝不退第二笔。
    """
    service = ChatService(session, auth)
    request_id = getattr(request.state, "request_id", None)
    try:
        if not payload.confirm:
            service.reject_action(action_id, payload.note)
            return ConfirmActionResponse(
                action_id=action_id,
                status="rejected",
                reason_code=ReasonCode.OK,
                replay=False,
                request_id=request_id,
            )
        outcome = service.confirm_action(action_id)
    except ActionNotFoundError as exc:
        raise NotFoundError("资源不存在") from exc
    except ServiceActionExpired as exc:
        raise ActionExpiredError() from exc
    except ActionStateError as exc:
        raise ActionStateConflictError() from exc

    return ConfirmActionResponse(
        action_id=outcome.action.id,
        status=outcome.action.status.value,
        reason_code=outcome.reason_code,
        replay=outcome.replay,
        result=dict(outcome.action.result) if outcome.action.result else None,
        request_id=request_id,
    )


def _to_message_response(outcome: TurnOutcome, request_id: str | None) -> MessageResponse:
    settings = get_settings()
    return MessageResponse(
        thread_id=outcome.thread_id,
        reply=outcome.reply,
        decision=outcome.decision,
        reason_code=outcome.reason_code,
        confidence="low" if outcome.confidence == "low" else "normal",
        citations=[
            CitationOut(policy_id=c.policy_id, policy_version=c.policy_version, anchor=c.anchor)
            for c in outcome.citations
        ],
        tools_used=outcome.tools_used,
        pending_action=_to_pending_action(outcome),
        handoff_offer=outcome.handoff_offer,
        usage=UsageOut(
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
            estimated_cost_usd=round(
                estimate_cost_usd(outcome.usage, settings.llm_model_primary), 6
            ),
        ),
        latency_ms=outcome.latency_ms,
        request_id=request_id,
    )


def _to_pending_action(outcome: TurnOutcome) -> PendingActionOut | None:
    """动作已落 `agent_actions`，回带真实的 action_id、确认地址与过期时间。

    落库失败或缺订单事实时三者为 null，前端据此把按钮置灰——绝不编造 action_id。
    """
    draft = outcome.pending_action
    if draft is None:
        return None
    return PendingActionOut(
        action_id=str(draft.action_id) if draft.action_id is not None else None,
        type=draft.type,
        summary=PendingActionSummary(
            order_id=draft.order_id, amount=draft.amount, currency=draft.currency
        ),
        policy_id=draft.policy_id,
        policy_version=draft.policy_version,
        confirm_url=(
            f"/v1/actions/{draft.action_id}/confirm" if draft.action_id is not None else None
        ),
        expires_at=draft.expires_at,
    )


def register_dev_routes(app_router: APIRouter) -> None:
    """dev-only：签发调试 token。**只在 `APP_ENV=dev` 时注册**，生产里这个路由不存在。

    它是唯一不要求 token 的 `/v1` 路由（否则拿不到第一个 token），
    因此中间件的 PUBLIC_PATHS 也放行它——两处必须同步。
    """

    @app_router.post("/dev/token", tags=["dev"])
    def dev_token(payload: DevTokenRequest) -> DevTokenResponse:
        settings = get_settings()
        token = issue_token(payload.user_id, [Role(r) for r in payload.roles])
        return DevTokenResponse(token=token, expires_in_minutes=settings.jwt_expire_minutes)


if get_settings().app_env == "dev":
    register_dev_routes(router)


__all__ = ["Response", "router"]
