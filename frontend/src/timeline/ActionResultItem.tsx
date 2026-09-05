/**
 * 动作执行回执（`POST /v1/actions/{id}/confirm` 的响应）。
 *
 * 它不是一句对话回复，所以单独成条：状态、金额、退款单号、幂等标记都要如实显示。
 * 两处刻意的措辞：
 * - `replay=true` 明说"未重复扣款"，这正是幂等键在起作用的可见证据；
 * - `simulated=true` 明说是模拟执行，别让人以为真退了钱。
 */

import type { ConfirmActionResponse } from '../api/types'

interface Props {
  result: ConfirmActionResponse
}

const STATUS_TEXT: Record<string, string> = {
  succeeded: '已执行',
  rejected: '已取消',
  failed: '执行失败',
}

export default function ActionResultItem({ result }: Props) {
  const rejected = result.status === 'rejected'
  const failed = result.status === 'failed'
  const tone = failed ? 'deny' : rejected ? 'degrade' : 'confirm'
  const refund = result.result

  return (
    <div className="turn assistant">
      <div className={`bubble assistant-bubble tone-border-${tone}`}>
        <div className={`banner tone-${tone}`}>
          {STATUS_TEXT[result.status] ?? result.status}
          {rejected ? '：本次动作已放弃，订单保持原状。' : ''}
          {result.replay && '（幂等重放：返回上一次的结果，未重复扣款）'}
        </div>

        {refund && (
          <dl className="kv">
            {refund.amount !== undefined && (
              <>
                <dt>金额</dt>
                <dd className="amount">¥{refund.amount}</dd>
              </>
            )}
            {refund.refund_id !== undefined && (
              <>
                <dt>退款单</dt>
                <dd>
                  <code>{refund.refund_id}</code>
                </dd>
              </>
            )}
            {refund.status !== undefined && (
              <>
                <dt>状态</dt>
                <dd>{refund.status}</dd>
              </>
            )}
          </dl>
        )}

        {refund?.simulated === true && (
          <p className="hint">模拟执行（RefundService SIMULATED）：账面已写入 biz.refunds，但没有真实出款。</p>
        )}

        <div className="row badges">
          <span className={`badge tone-${tone}`}>action {result.action_id}</span>
          <span className="badge">{result.reason_code}</span>
          {result.replay && <span className="badge tone-degrade">replay</span>}
          {result.request_id && <span className="badge">{result.request_id}</span>}
        </div>
      </div>
    </div>
  )
}
