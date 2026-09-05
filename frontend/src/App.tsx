/**
 * 应用外壳：内存里的会话身份 + 一个极简 hash 路由（不引路由库）。
 *
 * 登录后有三个页面共用同一份会话状态（useWorkspace 在 Workspace 组件里调一次）：
 *   #/chat   客户界面（默认）——只有对话，没有后台字段
 *   #/admin  客服工作台——三栏，判定 / 引用 / 工具 / 用量全展示
 *   #/review 人工审批占位
 * 客户在 #/chat 聊的会话，切到 #/admin 能看到同一条的判定细节。
 *
 * token 只放内存：刷新页面就要重新登录，demo 不做持久化。
 */

import { useCallback, useMemo, useState } from 'react'

import { createApiClient } from './api'
import { getToken, setToken } from './api/session'
import type { ApiClient, WhoAmI } from './api/types'
import Chat from './pages/Chat'
import CustomerChat from './pages/CustomerChat'
import Login, { type Entry } from './pages/Login'
import Review from './pages/Review'
import { useHashRoute } from './useHashRoute'
import { useWorkspace } from './useWorkspace'

interface WorkspaceProps {
  client: ApiClient
  identity: WhoAmI
  route: string
  onLogout: () => void
}

function Workspace({ client, identity, route, onLogout }: WorkspaceProps) {
  const ws = useWorkspace(client, onLogout)
  if (route === '/review') return <Review identity={identity} onLogout={onLogout} />
  if (route === '/admin') return <Chat client={client} identity={identity} ws={ws} onLogout={onLogout} />
  return <CustomerChat client={client} ws={ws} onLogout={onLogout} />
}

export default function App() {
  const [identity, setIdentity] = useState<WhoAmI | null>(null)
  const route = useHashRoute()

  const client = useMemo(() => createApiClient(getToken), [])

  /** 先写 token 再 whoami：以服务端认定的身份为准，失败就把 token 丢掉。 */
  const login = useCallback(
    async (token: string, entry: Entry) => {
      setToken(token)
      try {
        const who = await client.whoami()
        setIdentity(who)
        window.location.hash = entry === 'admin' ? '#/admin' : '#/chat'
        return who
      } catch (cause) {
        setToken(null)
        setIdentity(null)
        throw cause
      }
    },
    [client],
  )

  const logout = useCallback(() => {
    setToken(null)
    setIdentity(null)
    window.location.hash = '#/login'
  }, [])

  if (!identity) return <Login client={client} onLogin={login} />
  return <Workspace client={client} identity={identity} route={route} onLogout={logout} />
}
