/** 请求在途的占位。结果或错误到达时被 reducer 就地替换。 */

import { Spinner } from '../components/Icon'

interface Props {
  hint?: string
}

export default function WaitingItem({ hint }: Props) {
  return (
    <div className="turn turn-assistant">
      <div className="waiting" role="status" aria-live="polite">
        <div className="waiting-row">
          <Spinner />
          <span>{hint ?? '正在判定…'}</span>
        </div>
        <div className="progress" />
      </div>
    </div>
  )
}
