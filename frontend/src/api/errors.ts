/**
 * 错误的展示口径（§8.4）。规则写在一处，页面只管渲染。
 *
 * 关键一条：**404 不区分"不存在"与"不属于你"**——文案必须两者合一，
 * 否则前端就把后端刻意隐藏的存在性又暴露回去了。
 */

import { ApiError } from './types'

export interface ErrorView {
  title: string
  detail: string
  /** 是否给"重试"按钮 */
  retryable: boolean
  /** 是否踢回登录页 */
  needsLogin: boolean
  code: string
  requestId: string | null
}

export function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause
  return new ApiError({
    status: 0,
    code: 'INTERNAL_ERROR',
    message: cause instanceof Error ? cause.message : String(cause),
  })
}

export function describeError(cause: unknown): ErrorView {
  const error = toApiError(cause)
  const base = { code: error.code, requestId: error.requestId }

  switch (error.status) {
    case 401:
      return { ...base, title: '登录已失效', detail: '请重新登录。', retryable: false, needsLogin: true }
    case 404:
      return {
        ...base,
        title: '会话不存在或不属于你',
        detail: '换一个会话再试。',
        retryable: false,
        needsLogin: false,
      }
    case 429:
      return { ...base, title: '请求太频繁', detail: '稍等片刻再试。', retryable: true, needsLogin: false }
    case 503:
      return {
        ...base,
        title: '依赖暂时不可用',
        detail: '后端已降级，可以稍后重试。',
        retryable: true,
        needsLogin: false,
      }
    case 504:
      return { ...base, title: '模型响应超时', detail: '可以重试这一轮。', retryable: true, needsLogin: false }
    default:
      return {
        ...base,
        title: error.code,
        detail: error.message,
        retryable: error.retryable,
        needsLogin: false,
      }
  }
}

/** fetch 的 AbortError 不是错误，是用户/卸载主动取消，不该弹到界面上。 */
export function isAbort(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === 'AbortError'
}
