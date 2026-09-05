/** 对话页（接入中，下一个 milestone 补全）。 */

import type { ApiClient, WhoAmI } from '../api/types'

interface Props {
  client: ApiClient
  identity: WhoAmI
  onLogout: () => void
}

export default function Chat({ identity, onLogout }: Props) {
  return (
    <main className="shell">
      <h1>对话</h1>
      <p className="muted">
        已登录：user_id={identity.user_id} · roles={identity.roles.join(', ')}
      </p>
      <button className="btn" onClick={onLogout}>
        退出登录
      </button>
    </main>
  )
}
