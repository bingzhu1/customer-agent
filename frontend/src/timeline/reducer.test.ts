/**
 * reducer 单测：六种 decision 各一条 + 错误一条。
 * 不依赖 DOM，也不依赖网络——事件直接喂给纯函数。
 */

import { describe, expect, it } from 'vitest'

import { FIXTURES, NOT_FOUND_ERROR, makeResponse } from '../api/mock'
import type { Decision, MessageResponse } from '../api/types'
import {
  initialState,
  isWaiting,
  latestPendingAction,
  timelineReducer,
  type TimelineState,
} from './reducer'

/** 把一串事件按顺序折起来，模拟真实用法。 */
function fold(events: Parameters<typeof timelineReducer>[1][]): TimelineState {
  return events.reduce(timelineReducer, initialState)
}

/** 一问一答：用户消息 → 等待占位 → 最终结果替换占位。 */
function oneTurn(result: MessageResponse): TimelineState {
  return fold([
    { type: 'user.message', id: 'u1', text: '你好' },
    { type: 'waiting', id: 'a1' },
    { type: 'assistant.final', id: 'a1', result },
  ])
}

describe('timelineReducer 六种 decision', () => {
  const cases: Array<[string, MessageResponse, Decision]> = [
    ['ANSWER 正常回答带引用', FIXTURES.answer(), 'ANSWER'],
    ['REQUEST_INFO 缺实体', FIXTURES.request_info(), 'REQUEST_INFO'],
    ['REQUIRE_CONFIRMATION 89 元退款', FIXTURES.refund(), 'REQUIRE_CONFIRMATION'],
    ['REQUIRE_HUMAN 超限额', FIXTURES.human(), 'REQUIRE_HUMAN'],
    ['DENY 越权', FIXTURES.deny(), 'DENY'],
    ['DEGRADE 依赖不可用', FIXTURES.degrade(), 'DEGRADE'],
  ]

  it.each(cases)('%s', (_name, result, decision) => {
    const state = oneTurn(result)
    // 等待占位被替换，不是追加：两条条目而不是三条
    expect(state.items).toHaveLength(2)
    expect(isWaiting(state)).toBe(false)
    const last = state.items[1]
    expect(last.kind).toBe('assistant')
    if (last.kind !== 'assistant') throw new Error('unreachable')
    expect(last.result.decision).toBe(decision)
  })
})

describe('timelineReducer 关键分支', () => {
  it('错误事件替换等待占位，保留 request_id', () => {
    const state = fold([
      { type: 'user.message', id: 'u1', text: '看看我的会话' },
      { type: 'waiting', id: 'a1' },
      { type: 'error', id: 'a1', error: NOT_FOUND_ERROR },
    ])
    expect(state.items).toHaveLength(2)
    const last = state.items[1]
    expect(last.kind).toBe('error')
    if (last.kind !== 'error') throw new Error('unreachable')
    expect(last.error.status).toBe(404)
    expect(last.error.code).toBe('NOT_FOUND')
    expect(last.error.requestId).toBe('req_mock404')
  })

  it('等待中 isWaiting 为真', () => {
    const state = fold([{ type: 'waiting', id: 'a1' }])
    expect(isWaiting(state)).toBe(true)
  })

  it('latestPendingAction 取最新一条助手回复的待确认动作', () => {
    const withAction = oneTurn(FIXTURES.refund())
    expect(latestPendingAction(withAction)?.action_id).toBe('act_mock01')

    // 确认之后的新回复不再带 pending_action，按钮就该消失
    const after = timelineReducer(withAction, {
      type: 'assistant.final',
      id: 'a2',
      result: makeResponse({ reason_code: 'IDEMPOTENT_REPLAY', pending_action: null }),
    })
    expect(latestPendingAction(after)).toBeNull()
  })

  it('写路径未开：pending_action 仍在（金额与策略是真值），但没有 action_id', () => {
    const state = oneTurn(FIXTURES.refund_pending_only())
    const action = latestPendingAction(state)
    expect(action).not.toBeNull()
    expect(action?.action_id).toBeNull()
    expect(action?.confirm_url).toBeNull()
    // 判定该给的都给了，只差执行
    expect(action?.summary.amount).toBe('89.00')
    expect(action?.policy_id).toBe('REFUND-STD-001')
  })

  it('确认后的执行回执单独成条，且待确认动作不再可点', () => {
    const withAction = oneTurn(FIXTURES.refund())
    expect(latestPendingAction(withAction)?.action_id).toBe('act_mock01')

    const after = timelineReducer(withAction, {
      type: 'action.result',
      id: 'a2',
      result: {
        action_id: 166,
        status: 'succeeded',
        reason_code: 'POLICY_SATISFIED',
        replay: false,
        result: { refund_id: 9002, amount: '89.00', status: 'succeeded', simulated: true },
        request_id: 'req_x',
      },
    })
    const last = after.items[after.items.length - 1]
    expect(last.kind).toBe('action')
    if (last.kind !== 'action') throw new Error('unreachable')
    expect(last.result.result?.simulated).toBe(true)
  })

  it('同一动作已有回执时，按钮不再出现（不允许确认第二次）', () => {
    const withAction = oneTurn(FIXTURES.refund())
    const after = timelineReducer(withAction, {
      type: 'action.result',
      id: 'a2',
      result: {
        // 后端 action_id 是数字，pending_action 里是字符串，比较时要按字符串对齐
        action_id: 'act_mock01' as unknown as number,
        status: 'succeeded',
        reason_code: 'IDEMPOTENT_REPLAY',
        replay: true,
        result: null,
        request_id: null,
      },
    })
    expect(latestPendingAction(after)).toBeNull()
  })

  it('reset 用于拉历史，整条时间线被替换', () => {
    const state = timelineReducer(oneTurn(FIXTURES.answer()), {
      type: 'reset',
      items: [{ kind: 'user', id: 'h1', text: '历史消息' }],
    })
    expect(state.items).toHaveLength(1)
    expect(state.items[0].id).toBe('h1')
  })

  it('state 不被就地改写', () => {
    const before = oneTurn(FIXTURES.answer())
    const snapshot = before.items
    timelineReducer(before, { type: 'user.message', id: 'u2', text: '再问一句' })
    expect(before.items).toBe(snapshot)
    expect(before.items).toHaveLength(2)
  })
})
