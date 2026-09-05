/**
 * 时间线纯函数：`(state, event) => state`，不碰 DOM、不发请求，可脱离浏览器单测。
 *
 * 现在只有四种事件（非流式）。Phase 4 接 SSE 时再加 `token` / `tool_started` 等，
 * **state 与 item 的形状不变**——新事件只是往 items 里多写几种条目或就地改写。
 */

import type { ApiError, ConfirmActionResponse, MessageResponse } from '../api/types'

export type TimelineItem =
  | { kind: 'user'; id: string; text: string }
  /** 请求在途的占位；同 id 的 final / error 到达时被就地替换 */
  | { kind: 'waiting'; id: string; hint?: string }
  | { kind: 'assistant'; id: string; result: MessageResponse }
  | { kind: 'error'; id: string; error: ApiError }
  /** 确认 / 取消一个待执行动作后的执行回执（不是对话回复） */
  | { kind: 'action'; id: string; result: ConfirmActionResponse }

export type TimelineEvent =
  | { type: 'user.message'; id: string; text: string }
  | { type: 'waiting'; id: string; hint?: string }
  | { type: 'assistant.final'; id: string; result: MessageResponse }
  | { type: 'error'; id: string; error: ApiError }
  | { type: 'action.result'; id: string; result: ConfirmActionResponse }
  /** 拉历史：整条时间线换成给定条目（不是事件流的一部分，但走同一个 reducer） */
  | { type: 'reset'; items: TimelineItem[] }

export interface TimelineState {
  items: TimelineItem[]
}

export const initialState: TimelineState = { items: [] }

/** 有同 id 的占位就替换，否则追加——这样"等待 → 结果"不产生重复条目。 */
function upsert(items: TimelineItem[], item: TimelineItem): TimelineItem[] {
  const at = items.findIndex((existing) => existing.id === item.id)
  if (at === -1) return [...items, item]
  const next = items.slice()
  next[at] = item
  return next
}

export function timelineReducer(state: TimelineState, event: TimelineEvent): TimelineState {
  switch (event.type) {
    case 'user.message':
      return { items: upsert(state.items, { kind: 'user', id: event.id, text: event.text }) }
    case 'waiting':
      return { items: upsert(state.items, { kind: 'waiting', id: event.id, hint: event.hint }) }
    case 'assistant.final':
      return { items: upsert(state.items, { kind: 'assistant', id: event.id, result: event.result }) }
    case 'error':
      return { items: upsert(state.items, { kind: 'error', id: event.id, error: event.error }) }
    case 'action.result':
      return { items: upsert(state.items, { kind: 'action', id: event.id, result: event.result }) }
    case 'reset':
      return { items: event.items }
  }
}

/**
 * 最后一条待确认动作：只有最新一条助手回复里的 pending_action 才可操作。
 * 一旦这个动作已经有了执行回执，就不再返回它——按钮该消失，不该能点第二次。
 */
export function latestPendingAction(state: TimelineState) {
  const done = new Set(
    state.items.filter((item) => item.kind === 'action').map((item) => String(item.result.action_id)),
  )
  for (let i = state.items.length - 1; i >= 0; i--) {
    const item = state.items[i]
    if (item.kind === 'assistant') {
      const action = item.result.pending_action
      if (action !== null && action.action_id !== null && done.has(action.action_id)) return null
      return action
    }
  }
  return null
}

/** 是否有请求在途（用于禁用发送框）。 */
export function isWaiting(state: TimelineState): boolean {
  return state.items.some((item) => item.kind === 'waiting')
}
