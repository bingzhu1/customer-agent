/**
 * 后端契约的唯一事实来源（PRD §8.2 响应体、§8.4 错误信封、§9.3 六值终态、§9.5 reason_code）。
 *
 * 约定：后端字段一旦与这里对不上，**只改本文件**，其余代码不动。
 */

/** §9.3 DecisionOutcome，六值终态。`continue` / `retry` 是内部信号，不在此列。 */
export const DECISIONS = [
  'ANSWER',
  'REQUEST_INFO',
  'REQUIRE_CONFIRMATION',
  'REQUIRE_HUMAN',
  'DENY',
  'DEGRADE',
] as const
export type Decision = (typeof DECISIONS)[number]

/** §9.5 reason_code 枚举。 */
export const REASON_CODES = [
  'OK',
  'MISSING_ENTITY',
  'POLICY_SATISFIED',
  'POLICY_VIOLATION_WINDOW',
  'POLICY_VIOLATION_CATEGORY',
  'POLICY_VIOLATION_CONDITION',
  'POLICY_AMBIGUOUS',
  'AMOUNT_ABOVE_AUTO_LIMIT',
  'LOW_CONFIDENCE_ON_DECISION',
  'OWNERSHIP_MISMATCH',
  'AUTH_INSUFFICIENT',
  'SUSPECTED_INJECTION',
  'RETRIEVAL_NO_RESULT',
  'RETRIEVAL_LOW_CONFIDENCE',
  'DEPENDENCY_UNAVAILABLE',
  'TOOL_FAILURE_REPEATED',
  'TOOL_BUDGET_EXCEEDED',
  'CUSTOMER_ESCALATION_REQUEST',
  'HIGH_NEGATIVE_SENTIMENT',
  'IDEMPOTENT_REPLAY',
] as const
export type ReasonCode = (typeof REASON_CODES)[number]

/**
 * 后端 `MessageResponse.confidence` 是 Literal["low", "normal"]（见 api/schemas.py）；
 * PRD §8.2 的示例写的是 "high"，这里两者都容纳，展示时只区分"是不是 low"。
 */
export type Confidence = 'low' | 'normal' | 'high'

/** §9.2：引用必须回带策略 id 与版本，前端据此展示"依据哪条规则的哪个版本"。 */
export interface Citation {
  policy_id: string
  /** 后端可空（CitationOut.policy_version: int | None） */
  policy_version: number | null
  anchor: string | null
}

export interface Usage {
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number
}

/** 金额明细。注意 `amount` 是**字符串**（后端用 Decimal 序列化，避免浮点误差）。 */
export interface PendingActionSummary {
  order_id?: number | null
  amount: string | null
  currency: string | null
}

/**
 * §5.3 的待确认动作，字段与后端 `PendingActionOut` 对齐。
 *
 * Phase 4 之前**不落 `agent_actions` 表**，所以 `action_id` / `confirm_url` /
 * `expires_at` 恒为 null——卡片照常渲染（金额与策略引用是真值），确认按钮置灰。
 * 后端刻意不编一个假 action_id，前端也不能自己造一个。
 */
export interface PendingAction {
  action_id: string | null
  type: string
  summary: PendingActionSummary
  policy_id: string | null
  policy_version: number | null
  confirm_url: string | null
  expires_at: string | null
}

/** §8.2 非流式响应体。 */
export interface MessageResponse {
  thread_id: string
  reply: string
  decision: Decision
  reason_code: ReasonCode
  confidence: Confidence
  citations: Citation[]
  tools_used: string[]
  pending_action: PendingAction | null
  /** 后端是一句话文案（str | None），不是对象 */
  handoff_offer: string | null
  usage: Usage
  latency_ms: number
  request_id: string | null
}

export interface ThreadCreated {
  thread_id: string
  status: string
  created_at: string
}

/** `GET /v1/threads/{id}`：会话详情 + 消息 + CaseFacts 摘要（§8.1）。 */
export interface ThreadDetail {
  thread_id: string
  status: string
  created_at: string
  last_active_at: string
  messages: ThreadMessage[]
  /** CaseFacts 物化副本，只由确定性代码写入，前端只读（红线 3） */
  case_facts: Record<string, unknown>
  narrative_summary: string | null
}

/** 历史消息只有角色与文本，**不带**本轮的判定结果——判定不在历史里回放。 */
export interface ThreadMessage {
  role: string
  content: string
  created_at: string
}

export interface WhoAmI {
  user_id: number
  roles: string[]
}

/** §8.4 错误信封：`{"error": {...}, "request_id": "..."}`。 */
export interface ErrorEnvelope {
  error: {
    code: string
    message: string
    retryable: boolean
  }
  request_id?: string | null
  details?: unknown
}

export type ErrorCode =
  | 'INVALID_REQUEST'
  | 'UNAUTHENTICATED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'ACTION_STATE_CONFLICT'
  | 'ACTION_EXPIRED'
  | 'RATE_LIMITED'
  | 'INTERNAL_ERROR'
  | 'DEPENDENCY_UNAVAILABLE'
  | 'LLM_TIMEOUT'
  /** 网络断开 / CORS / 响应不是 JSON——不是后端定义的 code，前端自造。 */
  | 'NETWORK_ERROR'

/** 解析后的错误。展示文案由 uiMessage 决定（§8.4 的 404 不区分两种情况）。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode | string
  readonly retryable: boolean
  readonly requestId: string | null

  constructor(opts: {
    status: number
    code: ErrorCode | string
    message: string
    retryable?: boolean
    requestId?: string | null
  }) {
    super(opts.message)
    this.name = 'ApiError'
    this.status = opts.status
    this.code = opts.code
    this.retryable = opts.retryable ?? false
    this.requestId = opts.requestId ?? null
  }
}

/** 后端接口交付进度：未就绪的按钮置灰而不是报错（见 .env.example）。 */
export interface ApiCapabilities {
  /** `POST /v1/dev/token` 是否可用；否则登录页要用户粘贴 token */
  devToken: boolean
  /** `POST /v1/actions/{id}/confirm` 是否可用；否则确认按钮置灰 */
  confirm: boolean
  /** `GET /v1/threads/{id}` 是否可用；否则不拉历史 */
  getThread: boolean
}

/** client.ts 与 mock.ts 的共同接口。token 由 getToken 回调注入，不进函数签名。 */
export interface ApiClient {
  readonly capabilities: ApiCapabilities
  issueDevToken(userId: number): Promise<string>
  whoami(signal?: AbortSignal): Promise<WhoAmI>
  createThread(signal?: AbortSignal): Promise<ThreadCreated>
  sendMessage(threadId: string, text: string, signal?: AbortSignal): Promise<MessageResponse>
  getThread(threadId: string, signal?: AbortSignal): Promise<ThreadDetail>
  confirmAction(actionId: string, confirm: boolean, signal?: AbortSignal): Promise<MessageResponse>
}
