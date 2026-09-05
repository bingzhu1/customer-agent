/**
 * 客户界面（#/chat）：只有对话。
 *
 * 和工作台共用同一份会话状态，但这里**不出现任何后台字段**——
 * 没有 reason_code、置信度、工具调用、用量、request_id。判定只以人话体现：
 * 待确认 → 一张说人话的确认卡；转人工 → 一句温和提示；其余看回复正文就够。
 */

import { useEffect, useRef, useState } from 'react'

import type { ApiClient, MessageResponse, PendingAction } from '../api/types'
import Avatar from '../components/Avatar'
import Icon from '../components/Icon'
import { AGENT_PERSONA } from '../persona'
import { formatAmount } from '../timeline/decision'
import { isWaiting, latestPendingAction } from '../timeline/reducer'
import { activeThread } from '../timeline/workspace'
import type { Workspace } from '../useWorkspace'

interface Props {
  client: ApiClient
  ws: Workspace
  onLogout: () => void
}

const SUGGESTIONS = ['我想退款', '查一下我的订单', '物流到哪了']

/** 策略编号 → 人话。没对上的就原样给编号。 */
function policyName(policyId: string | null): string {
  if (!policyId) return '相关政策'
  if (policyId.startsWith('REFUND')) return '退款政策'
  if (policyId.startsWith('SHIP')) return '物流政策'
  if (policyId.startsWith('WARRANTY')) return '保修政策'
  if (policyId.startsWith('MEMBER')) return '会员政策'
  if (policyId.startsWith('COMPLAINT')) return '投诉处理政策'
  return policyId
}

function CustomerConfirmCard({
  action,
  actionable,
  confirmEnabled,
  busy,
  onConfirm,
}: {
  action: PendingAction
  actionable: boolean
  confirmEnabled: boolean
  busy: boolean
  onConfirm: (action: PendingAction, confirm: boolean) => void
}) {
  const clickable = actionable && confirmEnabled && action.action_id !== null && !busy
  const amount = formatAmount(action.summary.amount, action.summary.currency)
  return (
    <div className="c-card">
      <div className="c-card-head">请确认退款信息</div>
      <div className="c-card-grid">
        <div className="field">
          <span className="field-label">退款金额</span>
          <span className="num amount">{amount}</span>
        </div>
        <div className="field">
          <span className="field-label">订单号</span>
          <span className="num field-value">{action.summary.order_id ?? '—'}</span>
        </div>
        <div className="field">
          <span className="field-label">依据</span>
          <span className="field-value">{policyName(action.policy_id)}</span>
        </div>
        <div className="field">
          <span className="field-label">退回方式</span>
          <span className="field-value">原支付渠道，1–3 个工作日</span>
        </div>
      </div>
      <div className="c-card-foot">
        <button className="btn" disabled={!clickable} onClick={() => onConfirm(action, false)}>
          暂不退款
        </button>
        <button className="btn btn-primary" disabled={!clickable} onClick={() => onConfirm(action, true)}>
          确认退款 {amount}
        </button>
      </div>
      {!clickable && actionable && (
        <p className="c-card-note">退款申请暂时无法在线提交，{AGENT_PERSONA.name} 会为您跟进。</p>
      )}
    </div>
  )
}

function CustomerAssistantItem({
  result,
  actionable,
  confirmEnabled,
  busy,
  onConfirm,
}: {
  result: MessageResponse
  actionable: boolean
  confirmEnabled: boolean
  busy: boolean
  onConfirm: (action: PendingAction, confirm: boolean) => void
}) {
  const [showSource, setShowSource] = useState(false)
  const human = result.decision === 'REQUIRE_HUMAN'
  return (
    <div className="c-turn c-turn-agent">
      <Avatar size={32} />
      <div className="c-stack">
        <div className="c-bubble">{result.reply}</div>
        {result.pending_action && (
          <CustomerConfirmCard
            action={result.pending_action}
            actionable={actionable}
            confirmEnabled={confirmEnabled}
            busy={busy}
            onConfirm={onConfirm}
          />
        )}
        {human && (
          <div className="c-note">
            <Icon name="user" size={14} />
            已为您转接人工客服，{AGENT_PERSONA.name} 会尽快跟进。
          </div>
        )}
        {result.citations.length > 0 && (
          <div className="c-source">
            <button className="link small" onClick={() => setShowSource((open) => !open)}>
              {showSource ? '收起' : '查看依据的政策'}
            </button>
            {showSource && (
              <ul>
                {result.citations.map((citation) => (
                  <li key={`${citation.policy_id}-${citation.policy_version}`}>
                    {policyName(citation.policy_id)}
                    <span className="muted">
                      {' '}
                      · {citation.policy_id}
                      {citation.policy_version !== null && ` v${citation.policy_version}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function CustomerChat({ client, ws, onLogout }: Props) {
  const [draft, setDraft] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  const thread = activeThread(ws.state)
  const items = thread?.timeline.items ?? []
  const waiting = thread ? isWaiting(thread.timeline) : false
  const pending = thread ? latestPendingAction(thread.timeline) : null

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [items.length, thread?.id])

  const submit = () => {
    if (!thread || waiting || !draft.trim()) return
    void ws.send(thread.id, draft)
    setDraft('')
  }

  return (
    <div className="customer">
      <header className="c-head">
        <div className="c-head-left">
          <Avatar size={40} />
          <div className="c-head-text">
            <span className="c-head-title">{AGENT_PERSONA.title} 正在为您服务</span>
            <span className="muted small">
              <span className="online-dot" />
              {AGENT_PERSONA.tagline}
            </span>
          </div>
        </div>
        <div className="row">
          <a className="icon-btn" href="#/admin" aria-label="客服工作台" title="客服工作台">
            <Icon name="panel" size={16} />
          </a>
          <button className="icon-btn" onClick={onLogout} aria-label="退出" title="退出">
            <Icon name="logout" size={16} />
          </button>
        </div>
      </header>

      {ws.notice && (
        <div className="c-notice-bar">
          <span>刚才没有成功，请稍后再试。</span>
          <button className="link" onClick={ws.dismissNotice}>
            知道了
          </button>
        </div>
      )}

      <div className="c-timeline">
        <div className="c-timeline-inner">
          {!thread && !ws.creating && (
            <div className="c-turn c-turn-agent">
              <Avatar size={32} />
              <div className="c-stack">
                <div className="c-bubble">连接客服时出了点问题。</div>
                <button className="chip" onClick={() => void ws.newThread()}>
                  重新连接
                </button>
              </div>
            </div>
          )}

          {items.length === 0 && thread && (
            <div className="c-turn c-turn-agent">
              <Avatar size={32} />
              <div className="c-stack">
                <div className="c-bubble">您好，我是 {AGENT_PERSONA.name}。订单、退款、物流的问题都可以直接问我。</div>
                <div className="row wrap">
                  {SUGGESTIONS.map((text) => (
                    <button key={text} className="chip" onClick={() => void ws.send(thread.id, text)} disabled={waiting}>
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {items.map((item) => {
            switch (item.kind) {
              case 'user':
                return (
                  <div className="c-turn c-turn-user" key={item.id}>
                    <div className="c-bubble c-bubble-user">{item.text}</div>
                  </div>
                )
              case 'waiting':
                return (
                  <div className="c-turn c-turn-agent" key={item.id}>
                    <Avatar size={32} />
                    <div className="c-bubble c-typing" role="status" aria-live="polite">
                      <span className="typing-dot" />
                      <span className="typing-dot" />
                      <span className="typing-dot" />
                      <span className="muted small">{AGENT_PERSONA.name} 正在输入…</span>
                    </div>
                  </div>
                )
              case 'error':
                return (
                  <div className="c-turn c-turn-agent" key={item.id}>
                    <Avatar size={32} />
                    <div className="c-stack">
                      <div className="c-bubble">抱歉，刚才这一条没有发送成功。</div>
                      {item.error.retryable && thread && (
                        <button className="chip" onClick={() => ws.retry(thread.id)}>
                          再试一次
                        </button>
                      )}
                    </div>
                  </div>
                )
              case 'action': {
                // 执行回执。对客户不讲 action_id / reason_code，只讲结果；
                // 但模拟执行与幂等重放必须如实说，不能让人以为真退了两笔。
                const receipt = item.result
                const money = receipt.result?.amount
                return (
                  <div className="c-turn c-turn-agent" key={item.id}>
                    <Avatar size={32} />
                    <div className="c-stack">
                      <div className="c-bubble">
                        {receipt.status === 'rejected'
                          ? '好的，这次退款已经取消，订单保持原状。'
                          : receipt.status === 'succeeded'
                            ? `退款已受理${money ? `，${money} 元` : ''}将原路退回你的支付账户。`
                            : '这笔动作没有执行成功，我已经记录下来。'}
                        {receipt.replay && '（这笔刚才已经提交过了，没有重复扣款。）'}
                      </div>
                      {receipt.result?.simulated === true && (
                        <span className="muted small">演示环境：退款为模拟执行，未真实出款。</span>
                      )}
                    </div>
                  </div>
                )
              }
              case 'assistant':
                return (
                  <CustomerAssistantItem
                    key={item.id}
                    result={item.result}
                    actionable={item.result.pending_action !== null && item.result.pending_action === pending}
                    confirmEnabled={client.capabilities.confirm}
                    busy={waiting}
                    onConfirm={(action, accept) => thread && void ws.confirm(thread.id, action, accept)}
                  />
                )
            }
          })}
          <div ref={endRef} />
        </div>
      </div>

      <footer className="c-composer">
        <div className="composer-box">
          <textarea
            className="composer-input"
            rows={1}
            value={draft}
            placeholder={waiting ? `${AGENT_PERSONA.name} 正在处理…` : '请输入您的问题'}
            disabled={!thread || waiting}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            aria-label="输入消息"
          />
          <button className="send-btn" disabled={!thread || waiting || draft.trim() === ''} onClick={submit} aria-label="发送">
            <Icon name="arrow-up" size={16} strokeWidth={2.2} />
          </button>
        </div>
        <p className="c-foot-note muted small">回复由智能客服生成，涉及退款等操作会先请您确认。</p>
      </footer>
    </div>
  )
}
