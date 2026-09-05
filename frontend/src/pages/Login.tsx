/**
 * 登录页：拿到 token → 调 `GET /v1/whoami` 回显**服务端认定的**身份。
 *
 * 登录成功的判据是 whoami 通过，不是"输入框里有字"——这样能在接线阶段
 * 立刻看出 token 是否真的被后端接受。
 */

import { useState } from 'react'

import { API_BASE, USE_MOCK } from '../api'
import { describeError } from '../api/errors'
import type { ApiClient, WhoAmI } from '../api/types'
import Icon from '../components/Icon'

/** 登录后进哪个界面：客户界面只有对话；工作台是三栏全量视图 */
export type Entry = 'customer' | 'admin'

interface Props {
  client: ApiClient
  /** 写入 token → 调 whoami → 成功则登录，失败抛错 */
  onLogin: (token: string, entry: Entry) => Promise<WhoAmI>
}

export default function Login({ client, onLogin }: Props) {
  const [userId, setUserId] = useState('101')
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [entry, setEntry] = useState<Entry>('customer')

  const canIssue = client.capabilities.devToken

  async function run(getToken: () => Promise<string>) {
    setBusy(true)
    setError(null)
    try {
      await onLogin(await getToken(), entry)
    } catch (cause) {
      const view = describeError(cause)
      setError(`${view.title}：${view.detail}（${view.code}）`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <div className="login-card">
        <div className="brand brand-lg">
          <Icon name="chat" size={20} strokeWidth={1.8} />
          <span>客服 Agent</span>
        </div>
        <p className="muted">
          {USE_MOCK ? 'mock 数据源，不需要后端，user_id 随便填。' : `后端 ${API_BASE}，身份以服务端 whoami 为准。`}
        </p>

        <div className="segmented" role="radiogroup" aria-label="进入方式">
          <button className={entry === 'customer' ? 'on' : ''} role="radio" aria-checked={entry === 'customer'} onClick={() => setEntry('customer')}>
            客户界面
          </button>
          <button className={entry === 'admin' ? 'on' : ''} role="radio" aria-checked={entry === 'admin'} onClick={() => setEntry('admin')}>
            客服工作台
          </button>
        </div>

        <section className="login-section">
          <label className="field-label" htmlFor="login-user">
            用 user_id 换取调试 token
          </label>
          <div className="row">
            <input
              id="login-user"
              className="input mono"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              inputMode="numeric"
            />
            <button
              className="btn btn-primary"
              disabled={busy || !canIssue || !/^\d+$/.test(userId)}
              onClick={() => run(() => client.issueDevToken(Number(userId)))}
            >
              登录
            </button>
          </div>
          {!canIssue && (
            <p className="hint">
              后端 <span className="mono">POST /v1/dev/token</span> 未就绪。本地跑{' '}
              <span className="mono">make token USER={userId || '101'}</span> 签一个，粘到下面。
            </p>
          )}
        </section>

        <div className="divider">
          <span>或</span>
        </div>

        <section className="login-section">
          <label className="field-label" htmlFor="login-token">
            直接粘贴 token
          </label>
          <textarea
            id="login-token"
            className="input mono"
            rows={3}
            value={pasted}
            placeholder="eyJhbGciOiJIUzI1NiIs..."
            onChange={(e) => setPasted(e.target.value)}
          />
          <div className="row end">
            <button className="btn" disabled={busy || pasted.trim().length === 0} onClick={() => run(async () => pasted.trim())}>
              用这个 token 登录
            </button>
          </div>
        </section>

        {busy && <p className="muted small">正在校验身份…</p>}
        {error && <p className="error-text">{error}</p>}
      </div>
    </main>
  )
}
