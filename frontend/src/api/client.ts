/**
 * 真实 HTTP 客户端。三条纪律：
 *
 * 1. 每个请求都带 `Authorization: Bearer <token>`，token 只存在内存里（刷新即丢，demo 不做持久化）；
 * 2. 失败响应按 §8.4 的统一信封解析成 `ApiError`，解析不出来也要给出可展示的 code；
 * 3. 请求体里**永远不带 user_id**——身份由后端从 token 认定（§9.6）。
 */

import { ApiError, type ApiClient, type ApiCapabilities, type ErrorEnvelope } from './types'
import type {
  ConfirmActionResponse,
  MessageResponse,
  ThreadCreated,
  ThreadDetail,
  WhoAmI,
} from './types'

export interface HttpClientOptions {
  baseUrl: string
  getToken: () => string | null
  capabilities: ApiCapabilities
}

function isEnvelope(body: unknown): body is ErrorEnvelope {
  if (typeof body !== 'object' || body === null) return false
  const error = (body as { error?: unknown }).error
  return typeof error === 'object' && error !== null && 'code' in error
}

/** HTTP 状态 → 兜底 code / 文案：后端没给信封时也要有话可说。 */
function fallbackCode(status: number): string {
  const table: Record<number, string> = {
    400: 'INVALID_REQUEST',
    401: 'UNAUTHENTICATED',
    403: 'FORBIDDEN',
    404: 'NOT_FOUND',
    409: 'ACTION_STATE_CONFLICT',
    410: 'ACTION_EXPIRED',
    429: 'RATE_LIMITED',
    500: 'INTERNAL_ERROR',
    503: 'DEPENDENCY_UNAVAILABLE',
    504: 'LLM_TIMEOUT',
  }
  return table[status] ?? 'INTERNAL_ERROR'
}

export function createHttpClient(options: HttpClientOptions): ApiClient {
  const { baseUrl, getToken, capabilities } = options

  async function request<T>(
    path: string,
    init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
  ): Promise<T> {
    const token = getToken()
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (init.body !== undefined) headers['Content-Type'] = 'application/json'
    if (token) headers.Authorization = `Bearer ${token}`

    let response: Response
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method: init.method ?? 'GET',
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        signal: init.signal,
      })
    } catch (cause) {
      // AbortError 原样抛出：调用方靠它区分"用户取消"与"真出错"
      if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
      throw new ApiError({
        status: 0,
        code: 'NETWORK_ERROR',
        message: `无法连接后端 ${baseUrl}，确认服务已启动或改用 mock 模式`,
        retryable: true,
      })
    }

    const raw: unknown = await response.json().catch(() => null)

    if (!response.ok) {
      if (isEnvelope(raw)) {
        throw new ApiError({
          status: response.status,
          code: raw.error.code,
          message: raw.error.message,
          retryable: raw.error.retryable,
          requestId: raw.request_id ?? null,
        })
      }
      throw new ApiError({
        status: response.status,
        code: fallbackCode(response.status),
        message: `请求失败（HTTP ${response.status}）`,
        retryable: response.status === 429 || response.status >= 503,
      })
    }

    return raw as T
  }

  return {
    capabilities,

    async issueDevToken(userId: number): Promise<string> {
      // dev-only 接口，签发前身份还不存在，所以这里是唯一允许传 user_id 的地方
      // 响应是 {token, token_type, expires_in_minutes}
      const body = await request<{ token?: string }>('/v1/dev/token', {
        method: 'POST',
        body: { user_id: userId },
      })
      const token = body.token
      if (!token) {
        throw new ApiError({
          status: 500,
          code: 'INTERNAL_ERROR',
          message: '/v1/dev/token 没有返回 token 字段',
        })
      }
      return token
    },

    whoami: (signal) => request<WhoAmI>('/v1/whoami', { signal }),

    createThread: (signal) => request<ThreadCreated>('/v1/threads', { method: 'POST', body: {}, signal }),

    sendMessage: (threadId, text, signal) =>
      request<MessageResponse>(`/v1/threads/${encodeURIComponent(threadId)}/messages`, {
        method: 'POST',
        // 字段名是 message；后端 schema 是 extra="forbid"，多一个字段就 400
        body: { message: text },
        signal,
      }),

    getThread: (threadId, signal) =>
      request<ThreadDetail>(`/v1/threads/${encodeURIComponent(threadId)}`, { signal }),

    // 响应是执行回执 ConfirmActionResponse，不是 §8.2 的对话响应
    confirmAction: (actionId, confirm, signal) =>
      request<ConfirmActionResponse>(`/v1/actions/${encodeURIComponent(actionId)}/confirm`, {
        method: 'POST',
        body: { confirm },
        signal,
      }),
  }
}
