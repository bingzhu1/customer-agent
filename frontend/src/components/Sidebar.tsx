/**
 * 左侧会话侧栏：本次会话里建过 / 打开过的 thread，最近活动在前。
 * 每条带最新判定的色点与一句摘要；底部是身份与数据源。
 */

import { useState } from 'react'

import type { WhoAmI } from '../api/types'
import { DECISION_STYLE, formatAmount, formatClock } from '../timeline/decision'
import type { ThreadSummary } from '../timeline/workspace'
import Icon from './Icon'

interface Props {
  threads: ThreadSummary[]
  identity: WhoAmI
  source: string
  creating: boolean
  canOpenById: boolean
  onNew: () => void
  onSelect: (id: string) => void
  onOpenById: (id: string) => void
}

function summaryLine(thread: ThreadSummary): string {
  if (!thread.decision) return '还没有消息'
  const label = DECISION_STYLE[thread.decision].label
  if (thread.pending?.summary.amount) {
    return `${label} · ${formatAmount(thread.pending.summary.amount, thread.pending.summary.currency)}`
  }
  return label
}

export default function Sidebar({ threads, identity, source, creating, canOpenById, onNew, onSelect, onOpenById }: Props) {
  const [openId, setOpenId] = useState('')

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <div className="brand">
          <Icon name="chat" size={18} strokeWidth={1.8} />
          <span>客服 Agent</span>
        </div>
        <button className="btn btn-sm" onClick={onNew} disabled={creating}>
          <Icon name="plus" size={14} />
          新会话
        </button>
      </div>

      <div className="section-label">会话</div>
      <div className="thread-list">
        {threads.length === 0 && <p className="muted small thread-empty">{creating ? '正在创建会话…' : '还没有会话'}</p>}
        {threads.map((thread) => {
          const tone = thread.decision ? DECISION_STYLE[thread.decision].tone : 'neutral'
          return (
            <button
              key={thread.id}
              className={`thread-item${thread.active ? ' active' : ''}`}
              onClick={() => onSelect(thread.id)}
              title={thread.id}
            >
              <div className="thread-row">
                <span className="thread-title">{thread.title}</span>
                <span className="thread-time num">{formatClock(thread.updatedAt)}</span>
              </div>
              <div className="thread-row muted small">
                <span className={`dot tone-dot-${tone}`} />
                <span className="thread-sub">{summaryLine(thread)}</span>
              </div>
            </button>
          )
        })}
      </div>

      <form
        className="open-by-id"
        onSubmit={(event) => {
          event.preventDefault()
          if (openId.trim()) onOpenById(openId.trim())
          setOpenId('')
        }}
      >
        <input
          className="input input-sm mono"
          value={openId}
          placeholder="按 thread_id 打开"
          onChange={(event) => setOpenId(event.target.value)}
          aria-label="thread_id"
          disabled={!canOpenById}
          title={canOpenById ? undefined : 'GET /v1/threads/{id} 未就绪'}
        />
      </form>

      <div className="sidebar-foot">
        <div className="identity">
          <span className="avatar num">{identity.user_id}</span>
          <span className="muted small">
            user_id {identity.user_id} · {identity.roles.join(', ')}
          </span>
        </div>
        <span className="mono muted small">{source}</span>
      </div>
    </aside>
  )
}
