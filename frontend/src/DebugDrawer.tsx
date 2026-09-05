/**
 * 右侧调试抽屉：本轮的工具调用、token 用量与成本、耗时、request_id。
 * demo 讲解时用来对着说"这一轮花了多少、用了哪些工具、去哪条 trace 查"。
 */

import type { MessageResponse } from './api/types'

interface Props {
  result: MessageResponse | null
  open: boolean
  onClose: () => void
}

export default function DebugDrawer({ result, open, onClose }: Props) {
  if (!open) return null

  return (
    <aside className="drawer">
      <div className="drawer-head">
        <strong>本轮调试信息</strong>
        <button className="link" onClick={onClose}>
          收起
        </button>
      </div>

      {!result ? (
        <p className="muted small">还没有回复。发一条消息后这里会显示本轮的工具与用量。</p>
      ) : (
        <>
          <h3>判定</h3>
          <dl className="kv">
            <dt>decision</dt>
            <dd>{result.decision}</dd>
            <dt>reason</dt>
            <dd>{result.reason_code}</dd>
            <dt>confidence</dt>
            <dd>{result.confidence}</dd>
          </dl>

          <h3>tools_used</h3>
          {result.tools_used.length === 0 ? (
            <p className="muted small">本轮没有调用工具</p>
          ) : (
            <ul className="plain">
              {result.tools_used.map((tool) => (
                <li key={tool}>
                  <code>{tool}</code>
                </li>
              ))}
            </ul>
          )}

          <h3>usage</h3>
          <dl className="kv">
            <dt>input</dt>
            <dd>{result.usage.input_tokens} tokens</dd>
            <dt>output</dt>
            <dd>{result.usage.output_tokens} tokens</dd>
            <dt>成本</dt>
            <dd>${result.usage.estimated_cost_usd.toFixed(4)}</dd>
          </dl>

          <h3>其他</h3>
          <dl className="kv">
            <dt>耗时</dt>
            <dd>{result.latency_ms} ms</dd>
            <dt>request</dt>
            <dd>
              <code>{result.request_id}</code>
            </dd>
            <dt>thread</dt>
            <dd>
              <code>{result.thread_id}</code>
            </dd>
          </dl>
        </>
      )}
    </aside>
  )
}
