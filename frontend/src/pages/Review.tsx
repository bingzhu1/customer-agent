/** 人工审批页占位：Phase 6 M6 才做，这里只保留路由入口。 */

import type { WhoAmI } from '../api/types'

interface Props {
  identity: WhoAmI
  onLogout: () => void
}

export default function Review({ identity, onLogout }: Props) {
  return (
    <main className="shell">
      <h1>人工审批队列</h1>
      <p className="muted">
        当前身份 user_id={identity.user_id} · roles={identity.roles.join(', ')}
      </p>
      <p className="hint">
        本页是 Phase 6 M6 的范围（待审列表 + approve / edit / reject），现在只占位。
      </p>
      <div className="row">
        <a className="btn" href="#/chat">
          回到对话
        </a>
        <button className="btn" onClick={onLogout}>
          退出登录
        </button>
      </div>
    </main>
  )
}
