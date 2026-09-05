/**
 * 登录页：拿到 token → 调 `GET /v1/whoami` 回显**服务端认定的**身份。
 *
 * 登录成功的判据是 whoami 通过，不是"输入框里有字"——这样能在接线阶段
 * 立刻看出 token 是否真的被后端接受。
 */

import { useState } from 'react'

import { USE_MOCK } from '../api'
import { describeError } from '../api/errors'
import type { ApiClient, WhoAmI } from '../api/types'

interface Props {
  client: ApiClient
  /** 写入 token → 调 whoami → 成功则登录，失败抛错 */
  onLogin: (token: string) => Promise<WhoAmI>
}

export default function Login({ client, onLogin }: Props) {
  const [userId, setUserId] = useState('101')
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canIssue = client.capabilities.devToken

  async function run(getToken: () => Promise<string>) {
    setBusy(true)
    setError(null)
    try {
      await onLogin(await getToken())
    } catch (cause) {
      const view = describeError(cause)
      setError(`${view.title}：${view.detail}（${view.code}）`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="shell">
      <h1>客服 Agent · demo</h1>
      <p className="muted">
        当前数据源：{USE_MOCK ? 'mock（不需要后端）' : '真实后端'}
      </p>

      <section className="card">
        <h2>用 user_id 换 token</h2>
        <div className="row">
          <input
            className="input"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            inputMode="numeric"
            aria-label="user_id"
          />
          <button
            className="btn primary"
            disabled={busy || !canIssue || !/^\d+$/.test(userId)}
            onClick={() => run(() => client.issueDevToken(Number(userId)))}
          >
            换取 token 并登录
          </button>
        </div>
        {!canIssue && (
          <p className="hint">
            后端 <code>POST /v1/dev/token</code> 尚未交付（P1 进行中），此按钮暂不可用。
            请用下面的粘贴框：本地跑 <code>make token USER={userId || '101'}</code> 签一个。
          </p>
        )}
      </section>

      <section className="card">
        <h2>或直接粘贴 token</h2>
        <textarea
          className="input mono"
          rows={3}
          value={pasted}
          placeholder="eyJhbGciOiJIUzI1NiIs..."
          onChange={(e) => setPasted(e.target.value)}
          aria-label="token"
        />
        <button
          className="btn"
          disabled={busy || pasted.trim().length === 0}
          onClick={() => run(async () => pasted.trim())}
        >
          用这个 token 登录
        </button>
      </section>

      {busy && <p className="muted">正在校验身份…</p>}
      {error && <p className="error-text">{error}</p>}
    </main>
  )
}
