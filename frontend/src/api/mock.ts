/**
 * 假后端：与 `client.ts` 实现同一个 `ApiClient` 接口，`VITE_USE_MOCK=1` 时替换。
 *
 * 六种 decision 各有一条样本（§9.3），reason_code 取自 §9.4 升级矩阵，
 * 另有一条 §8.4 的 404 错误信封。**这里的数据只用于演示与单测，不是后端行为的规范。**
 */

import {
  ApiError,
  type ApiClient,
  type MessageResponse,
  type ThreadDetail,
  type WhoAmI,
} from './types'

let seq = 0
const nextId = (prefix: string) => `${prefix}_mock${String(++seq).padStart(4, '0')}`

/** 构造一条响应，未给的字段用 ANSWER 的默认值补齐。 */
export function makeResponse(patch: Partial<MessageResponse> = {}): MessageResponse {
  return {
    thread_id: 'th_mock',
    reply: '标准商品自签收之日起 30 天内、未使用可全额退款。',
    decision: 'ANSWER',
    reason_code: 'OK',
    confidence: 'normal',
    citations: [{ policy_id: 'REFUND-STD-001', policy_version: 3, anchor: 'refund#standard' }],
    tools_used: ['search_policy'],
    pending_action: null,
    handoff_offer: null,
    usage: { input_tokens: 1820, output_tokens: 142, estimated_cost_usd: 0.0091 },
    latency_ms: 2140,
    request_id: nextId('req'),
    ...patch,
  }
}

/** 六种 decision 的样本。键名同时用作 mock 的关键词触发字。 */
export const FIXTURES: Record<string, () => MessageResponse> = {
  /** ANSWER：正常回答带引用 */
  answer: () => makeResponse(),

  /** ANSWER + 低置信（矩阵规则 14）：仍带引用，另给转人工入口 */
  low_confidence: () =>
    makeResponse({
      reply: '根据现有政策文档，这一情形我只能给出参考答复：一般按标准退换货处理。如需确认，可转人工。',
      reason_code: 'RETRIEVAL_LOW_CONFIDENCE',
      confidence: 'low',
      handoff_offer: '需要人工核对政策？可以转人工核对。',
    }),

  /** REQUEST_INFO：缺必需实体（矩阵规则 15） */
  request_info: () =>
    makeResponse({
      reply: '请提供需要查询的订单号，我来帮你确认物流状态。',
      decision: 'REQUEST_INFO',
      reason_code: 'MISSING_ENTITY',
      citations: [],
      tools_used: [],
      usage: { input_tokens: 640, output_tokens: 38, estimated_cost_usd: 0.0021 },
      latency_ms: 780,
    }),

  /** REQUIRE_CONFIRMATION：89 元退款通过策略，等用户确认（§5.3 旅程 C，矩阵规则 12） */
  refund: () =>
    makeResponse({
      reply: '订单 82913 符合标准退款政策，退款金额 89.00 元。请确认后我再提交。',
      decision: 'REQUIRE_CONFIRMATION',
      reason_code: 'POLICY_SATISFIED',
      citations: [{ policy_id: 'REFUND-STD-001', policy_version: 3, anchor: 'refund#standard' }],
      tools_used: ['get_order', 'search_policy'],
      // mock 演示的是 Phase 4 写路径打通后的样子：有 action_id 才能点确认。
      // 真实后端现在返回的 action_id / confirm_url / expires_at 都是 null，
      // 见下面的 refund_pending_only。
      pending_action: {
        action_id: 'act_mock01',
        type: 'refund',
        summary: { order_id: 82913, amount: '89.00', currency: 'CNY' },
        policy_id: 'REFUND-STD-001',
        policy_version: 3,
        confirm_url: '/v1/actions/act_mock01/confirm',
        expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
      },
      usage: { input_tokens: 2410, output_tokens: 96, estimated_cost_usd: 0.0128 },
      latency_ms: 3320,
    }),

  /** REQUIRE_HUMAN：金额超自动限额（矩阵规则 10） */
  human: () =>
    makeResponse({
      reply: '这笔 620.00 元的退款超过自动处理限额，已提交主管审批。',
      decision: 'REQUIRE_HUMAN',
      reason_code: 'AMOUNT_ABOVE_AUTO_LIMIT',
      citations: [{ policy_id: 'REFUND-STD-001', policy_version: 3, anchor: 'refund#standard' }],
      tools_used: ['get_order', 'search_policy'],
      handoff_offer: '已提交主管审批，预计 2 小时内答复。',
      latency_ms: 2980,
    }),

  /** DENY：请求对象不属于当前用户（矩阵规则 1，对外不暴露存在性） */
  deny: () =>
    makeResponse({
      reply: '未找到该订单。请核对订单号后再试。',
      decision: 'DENY',
      reason_code: 'OWNERSHIP_MISMATCH',
      citations: [],
      tools_used: ['get_order'],
      usage: { input_tokens: 910, output_tokens: 24, estimated_cost_usd: 0.0031 },
      latency_ms: 640,
    }),

  /** DEGRADE：关键依赖不可用（矩阵规则 7） */
  degrade: () =>
    makeResponse({
      reply: '物流系统暂时不可用，先按政策回答：标准商品 30 天内可退。物流明细稍后再查。',
      decision: 'DEGRADE',
      reason_code: 'DEPENDENCY_UNAVAILABLE',
      tools_used: ['search_policy', 'get_shipping'],
      handoff_offer: '需要立刻确认物流？可以转人工。',
      latency_ms: 5120,
    }),
  /** 当前后端的真实形状：卡片有真金额与真策略引用，但 action_id / 有效期为 null */
  refund_pending_only: () => {
    const base = FIXTURES.refund()
    return {
      ...base,
      pending_action: {
        ...base.pending_action!,
        action_id: null,
        confirm_url: null,
        expires_at: null,
      },
    }
  },
}

/** §8.4 的 404 信封：不存在与不属于你，对外同一个响应。 */
export const NOT_FOUND_ERROR = new ApiError({
  status: 404,
  code: 'NOT_FOUND',
  message: '资源不存在',
  retryable: false,
  requestId: 'req_mock404',
})

/** 关键词 → 样本。演示时照着输入即可走到对应分支。 */
function pick(text: string): () => MessageResponse {
  const t = text.toLowerCase()
  if (t.includes('写路径') || t.includes('未开')) return FIXTURES.refund_pending_only
  if (t.includes('退款') || t.includes('refund')) return FIXTURES.refund
  if (t.includes('620') || t.includes('人工') || t.includes('主管')) return FIXTURES.human
  if (t.includes('别人') || t.includes('他的') || t.includes('99999')) return FIXTURES.deny
  if (t.includes('物流') || t.includes('快递')) return FIXTURES.degrade
  if (t.includes('订单')) return FIXTURES.request_info
  if (t.includes('不确定') || t.includes('低置信')) return FIXTURES.low_confidence
  return FIXTURES.answer
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    })
  })

export function createMockClient(): ApiClient {
  let threadId = ''
  const history: ThreadDetail['messages'] = []
  /** 已确认过的动作 id，用来演示幂等重放 */
  const confirmed = new Set<string>()

  return {
    capabilities: { devToken: true, confirm: true, getThread: true },

    async issueDevToken(userId: number): Promise<string> {
      await sleep(120)
      return `mock.token.for.user.${userId}`
    },

    async whoami(): Promise<WhoAmI> {
      await sleep(120)
      return { user_id: 101, roles: ['customer'] }
    },

    async createThread() {
      await sleep(120)
      threadId = nextId('th')
      history.length = 0
      return { thread_id: threadId, status: 'active', created_at: new Date().toISOString() }
    },

    async sendMessage(id, text, signal) {
      await sleep(700, signal)
      // 演示 404：会话 id 里带 gone 就当作不存在或不属于你
      if (id.includes('gone')) throw NOT_FOUND_ERROR
      const result = { ...pick(text)(), thread_id: id }
      history.push({ role: 'user', content: text, created_at: new Date().toISOString() })
      history.push({ role: 'assistant', content: result.reply, created_at: new Date().toISOString() })
      return result
    },

    async getThread(id, signal) {
      await sleep(200, signal)
      if (id.includes('gone')) throw NOT_FOUND_ERROR
      return {
        thread_id: id,
        status: 'active',
        created_at: new Date().toISOString(),
        last_active_at: new Date().toISOString(),
        messages: [...history],
        case_facts: {},
        narrative_summary: null,
      }
    },

    async confirmAction(actionId, confirm, signal) {
      await sleep(500, signal)
      // 同一个动作确认第二次不再产生副作用，返回上一次的结果（FR-504）
      const replay = confirmed.has(actionId)
      confirmed.add(actionId)
      return confirm
        ? {
            action_id: Number(actionId.replace(/\D/g, '')) || 166,
            status: 'succeeded',
            reason_code: replay ? 'IDEMPOTENT_REPLAY' : 'POLICY_SATISFIED',
            replay,
            result: { refund_id: 9002, amount: '89.00', status: 'succeeded', simulated: true },
            request_id: nextId('req'),
          }
        : {
            action_id: Number(actionId.replace(/\D/g, '')) || 166,
            status: 'rejected',
            reason_code: 'OK',
            replay: false,
            result: null,
            request_id: nextId('req'),
          }
    },
  }
}
