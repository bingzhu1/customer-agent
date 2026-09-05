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
    <div className="turn assistant">
      <div className="bubble error-bubble">
        <strong>{view.title}</strong>
        <p>{view.detail}</p>
        <div className="row">
          <span className="badge tone-deny">{view.code}</span>
          {view.requestId && <span className="badge">request_id {view.requestId}</span>}
          {view.retryable && onRetry && (
            <button className="btn" onClick={onRetry}>
              重试这一轮
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
