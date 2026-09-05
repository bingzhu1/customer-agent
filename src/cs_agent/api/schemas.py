"""API 请求 / 响应模型。字段与 PRD §8.2 逐项对齐——**前端按这个写，不要改名**。

请求体里**不接受任何身份字段**：`extra="forbid"` 会直接拒掉多余字段，
即便有人塞了 `user_id`，身份也只来自 token（FR-802、ADR-0008）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cs_agent.domain.enums import DecisionOutcome, ReasonCode


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateThreadResponse(_Strict):
    thread_id: UUID
    status: str
    created_at: datetime


class SendMessageRequest(_Strict):
    """一轮用户发言。只有一个字段——身份、时间都由服务端决定。"""

    message: str = Field(min_length=1, max_length=4000)


class CitationOut(_Strict):
    policy_id: str
    policy_version: int | None = None
    anchor: str | None = None


class UsageOut(_Strict):
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class PendingActionSummary(_Strict):
    """待确认动作的金额明细。数值来自业务库，不来自对话。"""

    order_id: int | None = None
    amount: str | None = None
    currency: str | None = None


class PendingActionOut(_Strict):
    """待确认的写操作（PRD §8.3 `requires_confirmation` 事件的字段）。

    Phase 4 之前**不落 `agent_actions` 表**，因此 `action_id` / `confirm_url` /
    `expires_at` 恒为 null——前端可以据此渲染确认卡片但把按钮置灰。
    绝不在这里编一个假的 action_id：那会让"确认"指向一个不存在的动作。
    """

    action_id: str | None = None
    type: str
    summary: PendingActionSummary
    policy_id: str | None = None
    policy_version: int | None = None
    confirm_url: str | None = None
    expires_at: datetime | None = None


class MessageResponse(_Strict):
    """PRD §8.2 的非流式响应体。"""

    thread_id: UUID
    reply: str
    decision: DecisionOutcome
    reason_code: ReasonCode
    confidence: Literal["low", "normal"]
    citations: list[CitationOut]
    tools_used: list[str]
    #: 决策为 REQUIRE_CONFIRMATION 时给出结构；写路径 Phase 4 才开，
    #: 在那之前 action_id / confirm_url / expires_at 为 null（红线 2）
    pending_action: PendingActionOut | None = None
    handoff_offer: str | None
    usage: UsageOut
    latency_ms: float
    request_id: str | None


class MessageOut(_Strict):
    role: str
    content: str
    created_at: datetime


class ThreadDetailResponse(_Strict):
    """会话详情 + 消息 + CaseFacts 摘要（FR-104）。"""

    thread_id: UUID
    status: str
    created_at: datetime
    last_active_at: datetime
    messages: list[MessageOut]
    #: CaseFacts 物化副本；只由确定性代码写入，这里只读（红线 3、不变式 2）
    case_facts: dict[str, object]
    narrative_summary: str | None


class DevTokenRequest(_Strict):
    """仅开发环境可用。生产不注册这个路由。"""

    user_id: int = Field(gt=0)
    roles: list[str] = Field(default_factory=lambda: ["customer"])


class DevTokenResponse(_Strict):
    token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in_minutes: int
