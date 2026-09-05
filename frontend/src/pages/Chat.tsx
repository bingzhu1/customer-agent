/**
 * 客服工作台（#/admin）：左侧会话侧栏 · 中间对话流 · 右侧本轮判定。
 * 会话状态由 App 层的 useWorkspace 提供（与客户界面共用），这里只排版。
 */

import { useEffect, useRef, useState } from 'react'

import { API_BASE, USE_MOCK } from '../api'
import type { ApiClient, WhoAmI } from '../api/types'
import Icon from '../components/Icon'
import JudgmentPanel from '../components/JudgmentPanel'
import Sidebar from '../components/Sidebar'
import AssistantFinalItem from '../timeline/AssistantFinalItem'
import { DECISION_STYLE } from '../timeline/decision'
import ErrorItem from '../timeline/ErrorItem'
import { isWaiting, latestPendingAction } from '../timeline/reducer'
import UserMessageItem from '../timeline/UserMessageItem'
import WaitingItem from '../timeline/WaitingItem'
import { activeThread, latestResult, threadSummaries, threadTitle } from '../timeline/workspace'
import type { Workspace } from '../useWorkspace'

interface Props {
  client: ApiClient
  identity: WhoAmI
  ws: Workspace
  onLogout: () => void
}

const SUGGESTIONS = ['我要退款', '查一下我的订单', '620 元那笔怎么退', '物流到哪了', '99999 是别人的订单']

export default function Chat({ client, identity, ws, onLogout }: Props) {
  const [draft, setDraft] = useState('')
  const [panelOpen, setPanelOpen] = useState(true)
  const endRef = useRef<HTMLDivElement>(null)

  const thread = activeThread(ws.state)
  const items = thread?.timeline.items ?? []
  const waiting = thread ? isWaiting(thread.timeline) : false
  const pending = thread ? latestPendingAction(thread.timeline) : null
  const result = latestResult(thread)
  const status = result ? DECISION_STYLE[result.decision] : null

  // 新条目到达时滚到底部
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [items.length, thread?.id])

  const submit = () => {
    if (!thread || waiting || !draft.trim()) return
    void ws.send(thread.id, draft)
    setDraft('')
  }

  return (
    <div className="app">
      <Sidebar
        threads={threadSummaries(ws.state)}
        identity={identity}
        source={USE_MOCK ? 'mock' : API_BASE.replace(/^https?:\/\//, '')}
        creating={ws.creating}
        canOpenById={client.capabilities.getThread}
        onNew={() => void ws.newThread()}
        onSelect={ws.select}
        onOpenById={(id) => void ws.openById(id)}
      />

      <main className="main">
        <header className="topbar">
          <div className="row">
            <span className="topbar-title">{thread ? threadTitle(thread, 24) : '会话'}</span>
            {thread && <span className="mono muted small">{thread.id}</span>}
          </div>
          <div className="row">
            {status && (
              <span className={`pill tone-${status.tone}`}>
                <span className={`dot tone-dot-${status.tone}`} />
                {status.label}
              </span>
            )}
            <button className={`icon-btn${panelOpen ? ' on' : ''}`} onClick={() => setPanelOpen((open) => !open)} aria-label="判定面板" title="判定面板">
              <Icon name="panel" size={16} />
            </button>
            <a className="icon-btn" href="#/chat" aria-label="客户视图" title="客户视图">
              <Icon name="chat" size={16} />
            </a>
            <a className="icon-btn" href="#/review" aria-label="审批页" title="审批页">
              <Icon name="user" size={16} />
            </a>
            <button className="icon-btn" onClick={onLogout} aria-label="退出" title="退出">
              <Icon name="logout" size={16} />
            </button>
          </div>
        </header>

        {ws.notice && (
          <div className="notice-bar">
            <span>{ws.notice}</span>
            <button className="link" onClick={ws.dismissNotice}>
              知道了
            </button>
          </div>
        )}

        <div className="timeline">
          <div className="timeline-inner">
            {items.length === 0 && thread && (
              <div className="empty">
                <p className="muted">回复由模型生成，退款、拒绝、转人工由策略引擎判定。试试：</p>
                <div className="row wrap">
                  {SUGGESTIONS.map((text) => (
                    <button key={text} className="chip" onClick={() => void ws.send(thread.id, text)} disabled={waiting}>
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {items.map((item) => {
              switch (item.kind) {
                case 'user':
                  return <UserMessageItem key={item.id} text={item.text} />
                case 'waiting':
                  return <WaitingItem key={item.id} hint={item.hint} />
                case 'error':
                  return <ErrorItem key={item.id} error={item.error} onRetry={thread ? () => ws.retry(thread.id) : undefined} />
                case 'assistant':
                  return (
                    <AssistantFinalItem
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

        <footer className="composer">
          <div className="composer-box">
            <textarea
              className="composer-input"
              rows={1}
              value={draft}
              placeholder={waiting ? '等待本轮结果…' : pending?.action_id ? '回复"确认"，或直接点上面的按钮' : '说点什么…'}
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
          <div className="composer-hint muted small">
            <span>{USE_MOCK ? 'mock 数据，不需要后端' : `后端 ${API_BASE}`}</span>
            <span>Enter 发送 · Shift+Enter 换行</span>
          </div>
        </footer>
      </main>

      {panelOpen && <JudgmentPanel result={result} threadId={thread?.id ?? null} onClose={() => setPanelOpen(false)} />}
    </div>
  )
}
