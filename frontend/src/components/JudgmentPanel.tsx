/**
 * 右栏"本轮判定"：把 decision / reason_code / 置信 / 引用 / 工具 / 用量
 * 从对话流里拿出来常驻展示——这是 demo 讲"确定性判定"这条主线的地方。
 */

import type { MessageResponse } from '../api/types'
import { DECISION_STYLE, confidenceLabel } from '../timeline/decision'
import Icon from './Icon'

interface Props {
  result: MessageResponse | null
  threadId: string | null
  onClose: () => void
}

export default function JudgmentPanel({ result, threadId, onClose }: Props) {
  return (
    <aside className="panel">
      <div className="panel-head">
        <span className="panel-title">本轮判定</span>
        <div className="row">
          {result?.request_id && <span className="mono muted small">{result.request_id}</span>}
          <button className="icon-btn" onClick={onClose} aria-label="收起判定面板">
            <Icon name="x" size={14} />
          </button>
        </div>
      </div>

      {!result ? (
        <div className="panel-body">
          <p className="muted small">发一条消息后，这里显示这一轮的判定、引用与用量。</p>
        </div>
      ) : (
        <div className="panel-body">
          <section className="panel-section">
            <div className="decision-line">
              <span className={`dot dot-lg tone-dot-${DECISION_STYLE[result.decision].tone}`} />
              <span className="mono decision-code">{result.decision}</span>
            </div>
            <dl className="kv">
              <dt>原因</dt>
              <dd className="mono">{result.reason_code}</dd>
              <dt>置信</dt>
              <dd>{confidenceLabel(result.confidence)}</dd>
              <dt>说明</dt>
              <dd>{DECISION_STYLE[result.decision].label}</dd>
            </dl>
          </section>

          <section className="panel-section">
            <div className="section-label">引用</div>
            {result.citations.length === 0 ? (
              <p className="muted small">本轮无策略引用</p>
            ) : (
              <div className="stack">
                {result.citations.map((citation) => (
                  <div className="cite-card" key={`${citation.policy_id}-${citation.policy_version}-${citation.anchor}`}>
                    <div className="row between">
                      <span className="mono">{citation.policy_id}</span>
                      <span className="mono muted small">
                        {citation.policy_version !== null && `v${citation.policy_version}`}
                        {citation.anchor && ` · ${citation.anchor}`}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel-section">
            <div className="section-label">工具调用</div>
            {result.tools_used.length === 0 ? (
              <p className="muted small">本轮没有调用工具</p>
            ) : (
              <div className="stack tight">
                {result.tools_used.map((tool) => (
                  <div className="tool-line" key={tool}>
                    <Icon name="check" size={12} strokeWidth={2.4} className="ok" />
                    <span className="mono">{tool}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel-section">
            <div className="section-label">用量</div>
            <div className="stat-grid">
              <div className="stat">
                <span className="muted small">输入</span>
                <span className="num stat-value">{result.usage.input_tokens.toLocaleString()}</span>
              </div>
              <div className="stat">
                <span className="muted small">输出</span>
                <span className="num stat-value">{result.usage.output_tokens.toLocaleString()}</span>
              </div>
              <div className="stat">
                <span className="muted small">成本</span>
                <span className="num stat-value">${result.usage.estimated_cost_usd.toFixed(4)}</span>
              </div>
            </div>
            <div className="row between small muted">
              <span>耗时</span>
              <span className="num">{result.latency_ms.toLocaleString()} ms</span>
            </div>
            {threadId && (
              <div className="row between small muted">
                <span>会话</span>
                <span className="mono">{threadId}</span>
              </div>
            )}
          </section>
        </div>
      )}
    </aside>
  )
}
