/**
 * 应用外壳：内存里的会话身份 + 一个极简 hash 路由（不引路由库）。
 *
 * token 只放内存：刷新页面就要重新登录，demo 不做持久化，也就不存在
 * localStorage 里躺着一个长期有效 token 的问题。
 */

import { useCallback, useMemo, useState } from 'react'

import { createApiClient } from './api'
import { getToken, setToken } from './api/session'
import type { WhoAmI } from './api/types'
import Chat from './pages/Chat'
import Login from './pages/Login'
import Review from './pages/Review'
import { useHashRoute } from './useHashRoute'

export default function App() {
  const [identity, setIdentity] = useState<WhoAmI | null>(null)
  const route = useHashRoute()

  const client = useMemo(() => createApiClient(getToken), [])

  /** 先写 token 再 whoami：以服务端认定的身份为准，失败就把 token 丢掉。 */
  const login = useCallback(
    async (token: string) => {
      setToken(token)
      try {
        const who = await client.whoami()
        setIdentity(who)
        window.location.hash = '#/chat'
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
  if (route === '/review') return <Review identity={identity} onLogout={logout} />
  return <Chat client={client} identity={identity} onLogout={logout} />
}
