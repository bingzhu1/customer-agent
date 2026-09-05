/** 一轮失败。文案口径见 api/errors.ts（404 不区分不存在与不属于你）。 */

import { describeError } from '../api/errors'
import type { ApiError } from '../api/types'

interface Props {
  error: ApiError
  onRetry?: () => void
}

export default function ErrorItem({ error, onRetry }: Props) {
  const view = describeError(error)
  return (
    <div className="turn turn-assistant">
      <div className="error-card">
        <div className="error-text-block">
          <strong>{view.title}</strong>
          <span className="muted small">
            {view.detail} <span className="mono">{view.code}</span>
            {view.requestId && (
              <>
                {' · '}
                <span className="mono">{view.requestId}</span>
              </>
            )}
          </span>
        </div>
        {view.retryable && onRetry && (
          <button className="btn" onClick={onRetry}>
            重试这一轮
          </button>
        )}
      </div>
    </div>
  )
}
