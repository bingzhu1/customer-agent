/** `GET /v1/threads/{id}` 的历史消息 → 时间线条目。 */

import type { MessageResponse, ThreadDetail } from '../api/types'
import type { TimelineItem } from './reducer'

/**
 * 历史里只有角色与文本——判定结果不在历史中回放，
 * 所以这里补出的是一条"中性"回复：decision=ANSWER、无引用、用量为 0。
 * 不要把它当成当时的真实判定。
 */
function asPlainReply(threadId: string, content: string): MessageResponse {
  return {
    thread_id: threadId,
    reply: content,
    decision: 'ANSWER',
    reason_code: 'OK',
    confidence: 'normal',
    citations: [],
    tools_used: [],
    pending_action: null,
    handoff_offer: null,
    usage: { input_tokens: 0, output_tokens: 0, estimated_cost_usd: 0 },
    latency_ms: 0,
    request_id: null,
  }
}

export function historyToItems(detail: ThreadDetail): TimelineItem[] {
  return detail.messages.map((message, index) => {
    const id = `hist${index}`
    return message.role === 'user'
      ? { kind: 'user', id, text: message.content }
      : { kind: 'assistant', id, result: asPlainReply(detail.thread_id, message.content) }
  })
}
