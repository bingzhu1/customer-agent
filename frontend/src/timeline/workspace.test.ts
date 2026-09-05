/** 多会话工作区单测：打开 / 切换 / 转发事件 / 摘要。 */

import { describe, expect, it } from 'vitest'

import { FIXTURES } from '../api/mock'
import {
  activeThread,
  emptyWorkspace,
  latestResult,
  threadSummaries,
  threadTitle,
  workspaceReducer,
  type WorkspaceEvent,
} from './workspace'

function fold(events: WorkspaceEvent[]) {
  return events.reduce(workspaceReducer, emptyWorkspace)
}

describe('workspaceReducer', () => {
  it('打开两条会话，最近打开的在前且被激活', () => {
    const state = fold([
      { type: 'thread.open', id: 'th_1', now: 1 },
      { type: 'thread.open', id: 'th_2', now: 2 },
    ])
    expect(state.order).toEqual(['th_2', 'th_1'])
    expect(state.activeId).toBe('th_2')
  })

  it('select 只切激活，不改顺序；未知 id 忽略', () => {
    const base = fold([
      { type: 'thread.open', id: 'th_1', now: 1 },
      { type: 'thread.open', id: 'th_2', now: 2 },
    ])
    const selected = workspaceReducer(base, { type: 'thread.select', id: 'th_1' })
    expect(selected.activeId).toBe('th_1')
    expect(selected.order).toEqual(['th_2', 'th_1'])
    expect(workspaceReducer(base, { type: 'thread.select', id: 'nope' })).toBe(base)
  })

  it('事件转发给对应时间线，并把该会话顶到最前', () => {
    const state = fold([
      { type: 'thread.open', id: 'th_1', now: 1 },
      { type: 'thread.open', id: 'th_2', now: 2 },
      { type: 'thread.event', id: 'th_1', now: 3, event: { type: 'user.message', id: 'u1', text: '我要退款' } },
      { type: 'thread.event', id: 'th_1', now: 4, event: { type: 'waiting', id: 'a1' } },
      { type: 'thread.event', id: 'th_1', now: 5, event: { type: 'assistant.final', id: 'a1', result: FIXTURES.refund() } },
    ])
    expect(state.order).toEqual(['th_1', 'th_2'])
    expect(state.threads.th_1.timeline.items).toHaveLength(2)
    expect(state.threads.th_2.timeline.items).toHaveLength(0)
    // 激活的仍是 th_2：转发事件不抢焦点
    expect(activeThread(state)?.id).toBe('th_2')
  })

  it('摘要：标题取第一条用户消息，decision 与 pending 取最新回复', () => {
    const state = fold([
      { type: 'thread.open', id: 'th_1', now: 1 },
      { type: 'thread.event', id: 'th_1', now: 2, event: { type: 'user.message', id: 'u1', text: '  我上周买的耳机想退，订单 82913  ' } },
      { type: 'thread.event', id: 'th_1', now: 3, event: { type: 'assistant.final', id: 'a1', result: FIXTURES.refund() } },
      { type: 'thread.open', id: 'th_2', now: 4 },
    ])
    const [second, first] = threadSummaries(state)
    expect(second.id).toBe('th_2')
    expect(second.title).toBe('新会话')
    expect(second.decision).toBeNull()
    expect(first.title).toBe('我上周买的耳机想退，订单 82913')
    expect(first.decision).toBe('REQUIRE_CONFIRMATION')
    expect(first.pending?.summary.amount).toBe('89.00')
    expect(threadTitle(state.threads.th_1, 6)).toBe('我上周买的耳…')
    expect(latestResult(state.threads.th_2)).toBeNull()
  })

  it('open 带 items 时整条时间线被替换（拉历史）', () => {
    const state = fold([
      { type: 'thread.open', id: 'th_1', now: 1 },
      { type: 'thread.event', id: 'th_1', now: 2, event: { type: 'user.message', id: 'u1', text: '旧' } },
      { type: 'thread.open', id: 'th_1', now: 3, items: [{ kind: 'user', id: 'h1', text: '历史' }] },
    ])
    expect(state.order).toEqual(['th_1'])
    expect(state.threads.th_1.timeline.items.map((item) => item.id)).toEqual(['h1'])
    expect(state.threads.th_1.createdAt).toBe(1)
  })
})
