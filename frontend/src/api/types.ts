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

export type Confidence = 'high' | 'medium' | 'low'

/** §9.2：引用必须回带策略 id 与版本，前端据此展示"依据哪条规则的哪个版本"。 */
export interface Citation {
  policy_id: string
  policy_version: number
  anchor: string
}

export interface Usage {
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number
}

/**
 * §5.3 的待确认动作。字段以 P1 实现为准，先按旅程 C 的
 * action_id / 金额明细 / policy 引用 / confirm_url / expires_at 定型。
 */
export interface PendingAction {
  action_id: string
  type: string
  summary: {
    order_id?: number
    amount: number
    currency: string
    [key: string]: unknown
  }
  policy_id: string
  policy_version: number
  confirm_url: string
  expires_at: string
}

/** §5.4：转人工时给出的入口；低置信回答（规则 14）也会带。 */
export interface HandoffOffer {
  review_id?: string
  message: string
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
  handoff_offer: HandoffOffer | null
  usage: Usage
  latency_ms: number
  request_id: string
}

export interface ThreadCreated {
  thread_id: string
}

/** `GET /v1/threads/{id}`：会话详情 + 消息 + CaseFacts 摘要（§8.1）。 */
export interface ThreadDetail {
  thread_id: string
  messages: ThreadMessage[]
  case_facts_summary?: Record<string, unknown> | null
}

export interface ThreadMessage {
  role: 'user' | 'assistant'
  content: string
  /** 助手消息带上本轮的判定结果；历史里可能缺，缺就只渲染文本。 */
  result?: Partial<MessageResponse> | null
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
