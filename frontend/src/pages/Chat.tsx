/**
 * 对话页：建会话 → 发消息 → 渲染判定结果。
 *
 * 两点刻意的做法：
 * - 所有请求都挂在一个 AbortController 上，组件卸载或换会话时中断，
 *   回调里再判一次 `isAbort`，避免对着已卸载的组件 dispatch；
 * - 时间线状态全部走 `timelineReducer`，页面本身不改条目数组，
 *   接 SSE 时只需要多 dispatch 几种事件。
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import { API_BASE, USE_MOCK } from '../api'
import { describeError, isAbort, toApiError } from '../api/errors'
import type { ApiClient, MessageResponse, PendingAction, WhoAmI } from '../api/types'
import DebugDrawer from '../DebugDrawer'
import { historyToItems } from '../timeline/history'
import AssistantFinalItem from '../timeline/AssistantFinalItem'
import ErrorItem from '../timeline/ErrorItem'
import UserMessageItem from '../timeline/UserMessageItem'
import WaitingItem from '../timeline/WaitingItem'
import {
  initialState,
  isWaiting,
  latestPendingAction,
  timelineReducer,
} from '../timeline/reducer'

interface Props {
  client: ApiClient
  identity: WhoAmI
  onLogout: () => void
}

export default function Chat({ client, identity, onLogout }: Props) {
  const [state, dispatch] = useReducer(timelineReducer, initialState)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [threadError, setThreadError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [historyInput, setHistoryInput] = useState('')

  const abortRef = useRef<AbortController | null>(null)
  const seqRef = useRef(0)
  const lastSentRef = useRef<string>('')
  const nextId = () => `evt${++seqRef.current}`

  /** 401 一律踢回登录页；其余错误留在时间线上。 */
  const handleFailure = useCallback(
    (id: string, cause: unknown) => {
      if (isAbort(cause)) return
      const error = toApiError(cause)
      if (describeError(error).needsLogin) {
        onLogout()
        return
      }
      dispatch({ type: 'error', id, error })
    },
    [onLogout],
  )

  // 进页面就建一个会话；卸载时中断在途请求
  useEffect(() => {
    const controller = new AbortController()
    abortRef.current = controller
    client
      .createThread(controller.signal)
      .then((created) => setThreadId(created.thread_id))
      .catch((cause: unknown) => {
        if (isAbort(cause)) return
        const view = describeError(cause)
        if (view.needsLogin) {
          onLogout()
          return
        }
        setThreadError(`${view.title}：${view.detail}（${view.code}）`)
      })
    return () => controller.abort()
  }, [client, onLogout])

  const send = useCallback(
    async (text: string) => {
      if (!threadId || text.trim().length === 0) return
      const content = text.trim()
      lastSentRef.current = content

      const controller = new AbortController()
      abortRef.current = controller
      const replyId = nextId()

      dispatch({ type: 'user.message', id: nextId(), text: content })
      dispatch({ type: 'waiting', id: replyId })
      setDraft('')

      try {
        const result = await client.sendMessage(threadId, content, controller.signal)
        dispatch({ type: 'assistant.final', id: replyId, result })
      } catch (cause) {
        handleFailure(replyId, cause)
      }
    },
    [client, threadId, handleFailure],
  )

  const confirm = useCallback(
    async (action: PendingAction, accept: boolean) => {
      // action_id 为 null 说明写路径未开，按钮本来就该是灰的；这里再兜一次
      if (action.action_id === null) return
      const controller = new AbortController()
      abortRef.current = controller
      const replyId = nextId()
      dispatch({ type: 'waiting', id: replyId, hint: accept ? '正在提交确认…' : '正在取消…' })
      try {
        const result = await client.confirmAction(action.action_id, accept, controller.signal)
        dispatch({ type: 'assistant.final', id: replyId, result })
      } catch (cause) {
        handleFailure(replyId, cause)
      }
    },
    [client, handleFailure],
  )

  /** 拉历史会话：接口未就绪时按钮置灰，这里只处理已就绪的情况。 */
  const loadHistory = useCallback(
    async (id: string) => {
      const target = id.trim()
      if (target === '') return
      const controller = new AbortController()
      abortRef.current = controller
      setThreadError(null)
      try {
        const detail = await client.getThread(target, controller.signal)
        setThreadId(detail.thread_id)
        dispatch({ type: 'reset', items: historyToItems(detail) })
      } catch (cause) {
        if (isAbort(cause)) return
        const view = describeError(cause)
        if (view.needsLogin) {
          onLogout()
          return
        }
        setThreadError(`${view.title}：${view.detail}（${view.code}）`)
      }
    },
    [client, onLogout],
  )

  /** 抽屉展示最新一轮的用量与工具。 */
  const latestResult: MessageResponse | null = (() => {
    for (let i = state.items.length - 1; i >= 0; i--) {
      const item = state.items[i]
      if (item.kind === 'assistant') return item.result
    }
    return null
  })()

  const waiting = isWaiting(state)
  const pending = latestPendingAction(state)

  return (
    <div className="chat">
      <header className="topbar">
        <div>
          <strong>客服 Agent · demo</strong>
          <span className="muted small">
            {' '}
            user_id={identity.user_id} · {identity.roles.join(', ')} ·{' '}
            {USE_MOCK ? 'mock 数据' : API_BASE}
            {threadId && ` · thread ${threadId}`}
          </span>
        </div>
        <div className="row">
          <button className="btn" onClick={() => setDrawerOpen((open) => !open)}>
            {drawerOpen ? '关闭调试' : '调试信息'}
          </button>
          <a className="btn" href="#/review">
            审批页
          </a>
          <button className="btn" onClick={onLogout}>
            退出
          </button>
        </div>
      </header>

      <div className="body">
        <main className="timeline">
          <div className="row history-bar">
            <input
              className="input"
              value={historyInput}
              placeholder={USE_MOCK ? '输入 thread_id 拉取历史（试 th_gone 看 404）' : '输入 thread_id 拉取历史'}
              onChange={(event) => setHistoryInput(event.target.value)}
              aria-label="thread_id"
            />
            <button
              className="btn"
              disabled={!client.capabilities.getThread || historyInput.trim() === ''}
              onClick={() => void loadHistory(historyInput)}
            >
              拉取历史
            </button>
            {!client.capabilities.getThread && (
              <span className="muted small">GET /v1/threads/{'{id}'} 未就绪</span>
            )}
          </div>

          {threadError && <p className="error-text">{threadError}</p>}
          {!threadId && !threadError && <p className="muted">正在创建会话…</p>}

          {state.items.length === 0 && threadId && (
            <p className="muted">
              试试：<code>我要退款</code>、<code>查一下我的订单</code>、<code>620 元那笔怎么退</code>、
              <code>物流到哪了</code>、<code>99999 是别人的订单</code>
            </p>
          )}

          {state.items.map((item) => {
            switch (item.kind) {
              case 'user':
                return <UserMessageItem key={item.id} text={item.text} />
              case 'waiting':
                return <WaitingItem key={item.id} hint={item.hint} />
              case 'error':
                return (
                  <ErrorItem
                    key={item.id}
                    error={item.error}
                    onRetry={() => void send(lastSentRef.current)}
                  />
                )
              case 'assistant':
                return (
                  <AssistantFinalItem
                    key={item.id}
                    result={item.result}
                    actionable={
                      item.result.pending_action !== null &&
                      item.result.pending_action.action_id === pending?.action_id
                    }
                    confirmEnabled={client.capabilities.confirm}
                    busy={waiting}
                    onConfirm={confirm}
                  />
                )
            }
          })}
        </main>

        <DebugDrawer result={latestResult} open={drawerOpen} onClose={() => setDrawerOpen(false)} />
      </div>

      <footer className="composer">
        <textarea
          className="input"
          rows={2}
          value={draft}
          placeholder={waiting ? '等待本轮结果…' : '说点什么（Enter 发送，Shift+Enter 换行）'}
          disabled={!threadId || waiting}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void send(draft)
            }
          }}
          aria-label="输入消息"
        />
        <button className="btn primary" disabled={!threadId || waiting || draft.trim() === ''} onClick={() => void send(draft)}>
          发送
        </button>
      </footer>
    </div>
  )
}
