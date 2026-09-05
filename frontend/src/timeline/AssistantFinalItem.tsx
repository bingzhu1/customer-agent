/**
 * 助手的最终回复。这条组件是整个 demo 的主角：它要让人一眼看到
 * **"LLM 说了什么" 与 "确定性代码判成了什么"是两回事**——
 * 回复文本归回复文本，decision / reason_code / 引用的策略版本单独成区。
 */

import { useState } from 'react'

import type { MessageResponse, PendingAction } from '../api/types'
import { DECISION_STYLE } from './decision'

interface Props {
  result: MessageResponse
  /** 只有最新一轮的待确认动作可操作，历史里的按钮不再出现 */
  actionable: boolean
  /** 后端 confirm 接口是否就绪；否则按钮置灰并注明 */
  confirmEnabled: boolean
  busy: boolean
  onConfirm: (action: PendingAction, confirm: boolean) => void
}

/** 金额是字符串（后端 Decimal 序列化），原样展示，前端不做数值换算。 */
function formatAmount(amount: string | null, currency: string | null) {
  if (amount === null) return '—'
  const symbol = currency === 'CNY' ? '¥' : ''
  return `${symbol}${amount}${currency ? ` ${currency}` : ''}`
}

function formatExpiry(iso: string | null) {
  if (iso === null) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  const minutes = Math.round((at.getTime() - Date.now()) / 60000)
  const left = minutes > 0 ? `还剩约 ${minutes} 分钟` : '已过期'
  return `${at.toLocaleString('zh-CN')}（${left}）`
}

function PendingActionPanel({ action, actionable, confirmEnabled, busy, onConfirm }: Props & { action: PendingAction }) {
  // 写路径（Phase 4）没开时后端不落 agent_actions，action_id 为 null：
  // 卡片照常渲染（金额与策略引用是真值），但没有可确认的对象，按钮必须置灰。
  const noAction = action.action_id === null
  const clickable = actionable && confirmEnabled && !noAction && !busy

  return (
    <div className="pending">
      <h3>待确认动作</h3>
      <dl className="kv">
        <dt>动作</dt>
        <dd>
          {action.type} {action.action_id ? <code>{action.action_id}</code> : <span className="muted">（尚未落库）</span>}
        </dd>
        {action.summary.order_id !== undefined && (
          <>
            <dt>订单</dt>
            <dd>{String(action.summary.order_id)}</dd>
          </>
        )}
        <dt>金额</dt>
        <dd className="amount">{formatAmount(action.summary.amount, action.summary.currency)}</dd>
        <dt>依据</dt>
        <dd>
          {action.policy_id ?? '—'}
          {action.policy_version !== null && <span className="muted"> v{action.policy_version}</span>}
        </dd>
        <dt>有效期</dt>
        <dd>{formatExpiry(action.expires_at)}</dd>
      </dl>
      <div className="row">
        <button className="btn primary" disabled={!clickable} onClick={() => onConfirm(action, true)}>
          确认执行
        </button>
        <button className="btn" disabled={!clickable} onClick={() => onConfirm(action, false)}>
          取消
        </button>
        {action.confirm_url && (
          <span className="muted">
            <code>POST {action.confirm_url}</code>
          </span>
        )}
      </div>
      {(noAction || !confirmEnabled) && (
        <p className="hint">
          写路径（Phase 4）尚未开通：后端还不落 <code>agent_actions</code>，没有 action_id 也没有
          <code> POST /v1/actions/{'{id}'}/confirm</code>，所以按钮置灰。
          上面的金额与策略引用是**真值**——判定已经跑完，只差执行这一步。
        </p>
      )}
    </div>
  )
}

export default function AssistantFinalItem(props: Props) {
  const { result } = props
  const [openCitations, setOpenCitations] = useState(false)
  const style = DECISION_STYLE[result.decision]
  const lowConfidence = result.confidence === 'low'

  return (
    <div className="turn assistant">
      <div className={`bubble assistant-bubble tone-border-${style.tone}`}>
        {style.banner && <div className={`banner tone-${style.tone}`}>{style.banner}</div>}

        <p className="reply">{result.reply}</p>

        {result.pending_action && <PendingActionPanel {...props} action={result.pending_action} />}

        {result.handoff_offer && <div className="banner tone-human">{result.handoff_offer}</div>}

        <div className="row badges">
          <span className={`badge tone-${style.tone}`}>
            {result.decision} · {style.label}
          </span>
          <span className="badge">{result.reason_code}</span>
          <span className={`badge${lowConfidence ? ' tone-degrade' : ''}`}>
            confidence: {result.confidence}
            {lowConfidence ? ' ⚠ 低置信' : ''}
          </span>
        </div>

        {lowConfidence && (
          <p className="hint">低置信回答：结论仅供参考，涉及资格或金额的判定请走人工确认。</p>
        )}

        {result.citations.length > 0 ? (
          <div className="citations">
            <button className="link" onClick={() => setOpenCitations((open) => !open)}>
              {openCitations ? '收起' : '展开'}引用（{result.citations.length}）
            </button>
            <ul>
              {result.citations.map((citation) => (
                <li key={`${citation.policy_id}-${citation.policy_version}-${citation.anchor}`}>
                  <span className="policy-id">{citation.policy_id}</span>
                  {citation.policy_version !== null && (
                    <span className="muted"> v{citation.policy_version}</span>
                  )}
                  {openCitations && (
                    <div className="anchor">
                      anchor: <code>{citation.anchor ?? '（无）'}</code>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="muted small">本轮无策略引用</p>
        )}
      </div>
    </div>
  )
}
