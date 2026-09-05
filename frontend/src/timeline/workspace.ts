/**
 * 多会话工作区：`(state, event) => state` 的纯函数，包住若干条 `TimelineState`。
 *
 * 会话列表目前只有"本次会话里建过或打开过的 thread"，不打后端列表接口——
 * `GET /v1/threads`（FR-109）交付后，只需在 `thread.open` 时把服务端返回的条目灌进来。
 * 每条时间线仍由 `timelineReducer` 处理，本文件不碰条目本身。
 */

import type { Decision, MessageResponse, PendingAction } from '../api/types'
import { initialState, timelineReducer, type TimelineEvent, type TimelineItem, type TimelineState } from './reducer'

export interface ThreadEntry {
  id: string
  createdAt: number
  updatedAt: number
  timeline: TimelineState
}

export interface WorkspaceState {
  activeId: string | null
  /** 最近活动在前 */
  order: string[]
  threads: Record<string, ThreadEntry>
}

export type WorkspaceEvent =
  /** 新建或按 id 打开一条会话并激活；带 items 时直接灌入（拉历史） */
  | { type: 'thread.open'; id: string; now: number; items?: TimelineItem[] }
  | { type: 'thread.select'; id: string }
  /** 转发给某条时间线的事件 */
  | { type: 'thread.event'; id: string; event: TimelineEvent; now: number }

export const emptyWorkspace: WorkspaceState = { activeId: null, order: [], threads: {} }

function bump(order: string[], id: string): string[] {
  return [id, ...order.filter((existing) => existing !== id)]
}

export function workspaceReducer(state: WorkspaceState, event: WorkspaceEvent): WorkspaceState {
  switch (event.type) {
    case 'thread.open': {
      const existing = state.threads[event.id]
      const timeline = event.items ? { items: event.items } : existing?.timeline ?? initialState
      const entry: ThreadEntry = {
        id: event.id,
        createdAt: existing?.createdAt ?? event.now,
        updatedAt: event.now,
        timeline,
      }
      return {
        activeId: event.id,
        order: bump(state.order, event.id),
        threads: { ...state.threads, [event.id]: entry },
      }
    }
    case 'thread.select':
      return state.threads[event.id] ? { ...state, activeId: event.id } : state
    case 'thread.event': {
      const existing = state.threads[event.id]
      if (!existing) return state
      const entry: ThreadEntry = {
        ...existing,
        updatedAt: event.now,
        timeline: timelineReducer(existing.timeline, event.event),
      }
      return {
        ...state,
        order: bump(state.order, event.id),
        threads: { ...state.threads, [event.id]: entry },
      }
    }
  }
}

/* ── 选择器：侧栏与右栏都从这里取，不各自遍历 ───────────── */

export function activeThread(state: WorkspaceState): ThreadEntry | null {
  return state.activeId ? state.threads[state.activeId] ?? null : null
}

/** 最近一条助手回复（右栏"本轮判定"的数据源） */
export function latestResult(entry: ThreadEntry | null): MessageResponse | null {
  if (!entry) return null
  for (let i = entry.timeline.items.length - 1; i >= 0; i--) {
    const item = entry.timeline.items[i]
    if (item.kind === 'assistant') return item.result
  }
  return null
}

/** 会话标题：第一条用户消息，截断；还没说话就叫"新会话" */
export function threadTitle(entry: ThreadEntry, max = 18): string {
  const first = entry.timeline.items.find((item) => item.kind === 'user')
  if (!first || first.kind !== 'user') return '新会话'
  const text = first.text.replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max)}…` : text
}

export interface ThreadSummary {
  id: string
  title: string
  updatedAt: number
  decision: Decision | null
  pending: PendingAction | null
  active: boolean
}

export function threadSummaries(state: WorkspaceState): ThreadSummary[] {
  return state.order.map((id) => {
    const entry = state.threads[id]
    const result = latestResult(entry)
    return {
      id,
      title: threadTitle(entry),
      updatedAt: entry.updatedAt,
      decision: result?.decision ?? null,
      pending: result?.pending_action ?? null,
      active: id === state.activeId,
    }
  })
}
