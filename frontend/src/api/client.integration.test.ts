/**
 * 对**真实后端**的联调冒烟测试：验证 client.ts 的请求体与解析和后端实际契约一致。
 *
 * 默认跳过——它要连 localhost:8000，还会真的调模型（一轮约 30 秒、几分钱）。
 * 跑法：先在仓库根 `make serve`（APP_ENV=dev），再
 *
 *   VITE_INTEGRATION=1 npm run test
 *
 * 断言只挑"契约层面会让前端渲染错"的点，不断言模型措辞。
 */

import { describe, expect, it } from 'vitest'

import { createHttpClient } from './client'
import { ApiError } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
const RUN = import.meta.env.VITE_INTEGRATION === '1'

describe.skipIf(!RUN)('client.ts ↔ 真实后端', () => {
  let token: string | null = null
  const client = createHttpClient({
    baseUrl: BASE,
    getToken: () => token,
    capabilities: { devToken: true, confirm: false, getThread: true },
  })

  it('dev token → whoami：身份以服务端认定为准', async () => {
    token = await client.issueDevToken(101)
    expect(token).toMatch(/\./)
    const who = await client.whoami()
    expect(who.user_id).toBe(101)
    expect(who.roles).toContain('customer')
  })

  it(
    '建会话 → 发消息：82913 走到 REQUIRE_CONFIRMATION，卡片有真金额但没有 action_id',
    async () => {
      const { thread_id } = await client.createThread()
      expect(thread_id).toBeTruthy()

      const result = await client.sendMessage(thread_id, '订单 82913 我要退款。')
      expect(result.decision).toBe('REQUIRE_CONFIRMATION')
      expect(result.reason_code).toBe('POLICY_SATISFIED')
      // 写路径 Phase 4 才开：id 三件套为 null，金额与策略引用是真值
      expect(result.pending_action?.action_id).toBeNull()
      expect(result.pending_action?.confirm_url).toBeNull()
      expect(result.pending_action?.summary.amount).toBe('89.00')
      expect(result.pending_action?.policy_id).toBe('REFUND-STD-001')
      expect(result.citations.length).toBeGreaterThan(0)
      // 前端要渲染的字段一个都不能少
      expect(['low', 'normal', 'high']).toContain(result.confidence)
      expect(typeof result.usage.estimated_cost_usd).toBe('number')
      expect(result.request_id).toBeTruthy()

      const detail = await client.getThread(thread_id)
      expect(detail.messages.length).toBeGreaterThanOrEqual(2)
      expect(detail.messages[0]).toHaveProperty('created_at')
    },
    120_000,
  )

  it('不存在或不属于自己的会话：404 NOT_FOUND，信封被解析成 ApiError', async () => {
    const missing = '00000000-0000-4000-8000-000000000000'
    await expect(client.getThread(missing)).rejects.toBeInstanceOf(ApiError)
    await client.getThread(missing).catch((error: ApiError) => {
      expect(error.status).toBe(404)
      expect(error.code).toBe('NOT_FOUND')
      expect(error.requestId).toBeTruthy()
    })
  })
})
