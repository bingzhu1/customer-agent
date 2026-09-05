/** `GET /v1/threads/{id}` 的历史消息 → 时间线条目。 */

import type { MessageResponse, ThreadDetail } from '../api/types'
import type { TimelineItem } from './reducer'

/** 历史里的助手消息可能只存了文本，缺的字段按"正常回答、无引用"补齐。 */
function hydrate(
  threadId: string,
  content: string,
  partial: Partial<MessageResponse> | null | undefined,
): MessageResponse {
  return {
    thread_id: threadId,
    reply: content,
    decision: 'ANSWER',
    reason_code: 'OK',
    confidence: 'high',
    citations: [],
    tools_used: [],
    pending_action: null,
    handoff_offer: null,
    usage: { input_tokens: 0, output_tokens: 0, estimated_cost_usd: 0 },
    latency_ms: 0,
    request_id: '',
    ...(partial ?? {}),
  }
}

export function historyToItems(detail: ThreadDetail): TimelineItem[] {
  return detail.messages.map((message, index) => {
    const id = `hist${index}`
    return message.role === 'user'
      ? { kind: 'user', id, text: message.content }
      : { kind: 'assistant', id, result: hydrate(detail.thread_id, message.content, message.result) }
  })
}
