/**
 * 工作区的副作用层：建会话、发消息、确认动作、按 id 拉历史、切换。
 * 页面只拿这里返回的状态与动作，不直接碰 client 或 reducer。
 *
 * 所有请求都挂在 AbortController 上：组件卸载时中断，回调里再判一次 `isAbort`，
 * 避免对着已卸载的组件 dispatch。
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import { describeError, isAbort, toApiError } from './api/errors'
import type { ApiClient, PendingAction } from './api/types'
import { historyToItems } from './timeline/history'
import type { TimelineEvent } from './timeline/reducer'
import { emptyWorkspace, workspaceReducer } from './timeline/workspace'

export type Workspace = ReturnType<typeof useWorkspace>

export function useWorkspace(client: ApiClient, onLogout: () => void) {
  const [state, dispatch] = useReducer(workspaceReducer, emptyWorkspace)
  const [creating, setCreating] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const controllers = useRef(new Set<AbortController>())
  const seqRef = useRef(0)
  const lastSentRef = useRef<Record<string, string>>({})
  const nextId = () => `evt${++seqRef.current}`

  const track = () => {
    const controller = new AbortController()
    controllers.current.add(controller)
    return controller
  }

  useEffect(() => {
    const live = controllers.current
    return () => live.forEach((controller) => controller.abort())
  }, [])

  const emit = useCallback((id: string, event: TimelineEvent) => {
    dispatch({ type: 'thread.event', id, event, now: Date.now() })
  }, [])

  /** 401 一律踢回登录页；其余错误交给调用方决定放哪。 */
  const failure = useCallback(
    (cause: unknown): string | null => {
      if (isAbort(cause)) return null
      const view = describeError(toApiError(cause))
      if (view.needsLogin) {
        onLogout()
        return null
      }
      return `${view.title}：${view.detail}（${view.code}）`
    },
    [onLogout],
  )

  const newThread = useCallback(async () => {
    setCreating(true)
    setNotice(null)
    const controller = track()
    try {
      const created = await client.createThread(controller.signal)
      dispatch({ type: 'thread.open', id: created.thread_id, now: Date.now() })
    } catch (cause) {
      const text = failure(cause)
      if (text) setNotice(text)
    } finally {
      controllers.current.delete(controller)
      setCreating(false)
    }
  }, [client, failure])

  // 进页面时如果一条会话都没有，就建一条。
  // 守卫在 cleanup 里重置：开发模式 StrictMode 会挂载→卸载→再挂载，卸载时上面的 effect
  // 把在途的 createThread 中断了，不重置就再也建不出会话（症状：输入框一直禁用）。
  const bootstrapped = useRef(false)
  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    void newThread()
    return () => {
      bootstrapped.current = false
    }
  }, [newThread])

  const select = useCallback((id: string) => dispatch({ type: 'thread.select', id }), [])

  const send = useCallback(
    async (threadId: string, text: string) => {
      const content = text.trim()
      if (!content) return
      lastSentRef.current[threadId] = content
      const controller = track()
      const replyId = nextId()
      emit(threadId, { type: 'user.message', id: nextId(), text: content })
      emit(threadId, { type: 'waiting', id: replyId })
      try {
        const result = await client.sendMessage(threadId, content, controller.signal)
        emit(threadId, { type: 'assistant.final', id: replyId, result })
      } catch (cause) {
        if (isAbort(cause)) return
        const text = failure(cause)
        if (text) emit(threadId, { type: 'error', id: replyId, error: toApiError(cause) })
      } finally {
        controllers.current.delete(controller)
      }
    },
    [client, emit, failure],
  )

  const retry = useCallback(
    (threadId: string) => {
      const last = lastSentRef.current[threadId]
      if (last) void send(threadId, last)
    },
    [send],
  )

  const confirm = useCallback(
    async (threadId: string, action: PendingAction, accept: boolean) => {
      // action_id 为 null 说明写路径未开，按钮本来就是灰的；这里再兜一次
      if (action.action_id === null) return
      const controller = track()
      const replyId = nextId()
      emit(threadId, { type: 'waiting', id: replyId, hint: accept ? '正在提交确认…' : '正在取消…' })
      try {
        const result = await client.confirmAction(action.action_id, accept, controller.signal)
        // 确认接口回的是执行回执，不是 §8.2 的对话响应，所以走单独的事件
        emit(threadId, { type: 'action.result', id: replyId, result })
      } catch (cause) {
        if (isAbort(cause)) return
        const text = failure(cause)
        if (text) emit(threadId, { type: 'error', id: replyId, error: toApiError(cause) })
      } finally {
        controllers.current.delete(controller)
      }
    },
    [client, emit, failure],
  )

  /** 按 id 打开会话：拉历史并激活。失败（含 404）显示在顶部提示条，不进时间线。 */
  const openById = useCallback(
    async (id: string) => {
      setNotice(null)
      const controller = track()
      try {
        const detail = await client.getThread(id, controller.signal)
        dispatch({ type: 'thread.open', id: detail.thread_id, now: Date.now(), items: historyToItems(detail) })
      } catch (cause) {
        const text = failure(cause)
        if (text) setNotice(text)
      } finally {
        controllers.current.delete(controller)
      }
    },
    [client, failure],
  )

  return { state, creating, notice, dismissNotice: () => setNotice(null), newThread, select, send, retry, confirm, openById }
}
