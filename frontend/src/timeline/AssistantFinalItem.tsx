/**
 * 助手的最终回复。整个页面的主角：让人一眼看到
 * **"LLM 说了什么" 与 "确定性代码判成了什么"是两回事**——
 * 工具调用一行、回复正文、（必要时）确认卡或说明卡、最后一行判定小字。
 * 判定的完整细节在右栏面板，这里只留够读懂的量。
 */

import { useState } from 'react'

import type { MessageResponse, PendingAction } from '../api/types'
import Icon from '../components/Icon'
import { DECISION_STYLE, confidenceLabel, formatAmount } from './decision'

interface Props {
  result: MessageResponse
  /** 只有最新一轮的待确认动作可操作，历史里的按钮不再出现 */
  actionable: boolean
  /** 后端 confirm 接口是否就绪；否则按钮置灰并注明 */
  confirmEnabled: boolean
  busy: boolean
  onConfirm: (action: PendingAction, confirm: boolean) => void
}

function formatExpiry(iso: string | null): string {
  if (iso === null) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  const minutes = Math.round((at.getTime() - Date.now()) / 60000)
  return minutes > 0 ? `约 ${minutes} 分钟` : '已过期'
}

function ToolCalls({ tools }: { tools: string[] }) {
  if (tools.length === 0) return null
  return (
    <div className="tool-calls">
      {tools.map((tool) => (
        <div className="tool-line" key={tool}>
          <Icon name="check" size={13} strokeWidth={2.4} className="ok" />
          <span className="mono">{tool}</span>
        </div>
      ))}
    </div>
  )
}

function ConfirmCard({ action, actionable, confirmEnabled, busy, onConfirm }: Props & { action: PendingAction }) {
  // 写路径（Phase 4）没开时后端不落 agent_actions，action_id 为 null：
  // 卡片照常渲染（金额与策略引用是真值），但没有可确认的对象，按钮必须置灰。
  const noAction = action.action_id === null
  const clickable = actionable && confirmEnabled && !noAction && !busy
  const amount = formatAmount(action.summary.amount, action.summary.currency)

  return (
    <div className="confirm-card">
      <div className="confirm-head">
        <span className="confirm-title">
          <Icon name="alert" size={14} />
          待你确认 · {action.type === 'refund' ? '退款' : action.type}
        </span>
        <span className="mono small">
          <Icon name="clock" size={12} /> 有效期 {formatExpiry(action.expires_at)}
        </span>
      </div>
      <div className="confirm-grid">
        <div className="field">
          <span className="field-label">金额</span>
          <span className="num amount">{amount}</span>
        </div>
        <div className="field">
          <span className="field-label">订单</span>
          <span className="mono field-value">{action.summary.order_id ?? '—'}</span>
        </div>
        <div className="field">
          <span className="field-label">依据</span>
          <span className="field-value">
            <span className="mono">{action.policy_id ?? '—'}</span>
            {action.policy_version !== null && <span className="muted"> v{action.policy_version}</span>}
          </span>
        </div>
        <div className="field">
          <span className="field-label">动作</span>
          <span className="field-value">
            {action.action_id ? <span className="mono">{action.action_id}</span> : <span className="muted">尚未落库</span>}
          </span>
        </div>
      </div>
      <div className="confirm-foot">
        <span className="mono muted small">{action.confirm_url ? `POST ${action.confirm_url}` : ''}</span>
        <div className="row">
          <button className="btn" disabled={!clickable} onClick={() => onConfirm(action, false)}>
            取消
          </button>
          <button className="btn btn-primary" disabled={!clickable} onClick={() => onConfirm(action, true)}>
            确认{action.type === 'refund' ? '退款' : '执行'} {amount}
          </button>
        </div>
      </div>
      {(noAction || !confirmEnabled) && (
        <p className="confirm-note">
          写路径（Phase 4）尚未开通：后端还不落 <span className="mono">agent_actions</span>，没有 action_id 也没有
          <span className="mono"> POST /v1/actions/{'{id}'}/confirm</span>，所以按钮置灰。金额与策略引用是真值，判定已经跑完，只差执行。
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
    <div className="turn turn-assistant">
      <div className="assistant">
        <ToolCalls tools={result.tools_used} />

        <p className="reply">{result.reply}</p>

        {result.pending_action && <ConfirmCard {...props} action={result.pending_action} />}

        {style.notice && (
          <div className={`notice tone-${style.tone}`}>
            <Icon name={style.icon} size={16} className="notice-icon" />
            <div className="notice-text">
              <strong>{style.notice}</strong>
              <span>
                {result.handoff_offer ?? ''}
                {result.handoff_offer ? ' ' : ''}
                <span className="mono">{result.reason_code}</span>
              </span>
            </div>
          </div>
        )}

        {!style.notice && result.handoff_offer && (
          <div className="notice tone-human">
            <Icon name="user" size={16} className="notice-icon" />
            <div className="notice-text">
              <span>{result.handoff_offer}</span>
            </div>
          </div>
        )}

        {lowConfidence && (
          <div className="notice tone-confirm">
            <Icon name="info" size={16} className="notice-icon" />
            <div className="notice-text">
              <span>低置信回答，仅供参考。涉及资格或金额的判定请走人工确认。</span>
            </div>
          </div>
        )}

        <div className="meta">
          <span className="meta-item">
            <span className={`dot tone-dot-${style.tone}`} />
            {style.label}
          </span>
          <span className="sep">·</span>
          <span className="mono meta-item">{result.reason_code}</span>
          <span className="sep">·</span>
          <span className="meta-item">置信 {confidenceLabel(result.confidence)}</span>
          {result.citations.length > 0 && (
            <>
              <span className="sep">·</span>
              <button className="link meta-item" onClick={() => setOpenCitations((open) => !open)}>
                <Icon name="doc" size={12} />
                {result.citations.map((c) => `${c.policy_id}${c.policy_version !== null ? ` v${c.policy_version}` : ''}`).join('，')}
              </button>
            </>
          )}
        </div>

        {openCitations && (
          <ul className="cite-list">
            {result.citations.map((citation) => (
              <li key={`${citation.policy_id}-${citation.policy_version}-${citation.anchor}`}>
                <span className="mono">{citation.policy_id}</span>
                {citation.policy_version !== null && <span className="muted"> v{citation.policy_version}</span>}
                <span className="muted"> · anchor </span>
                <span className="mono">{citation.anchor ?? '（无）'}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
