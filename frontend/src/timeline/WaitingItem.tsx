/** 请求在途的占位。结果或错误到达时被 reducer 就地替换。 */

interface Props {
  hint?: string
}

export default function WaitingItem({ hint }: Props) {
  return (
    <div className="turn assistant">
      <div className="bubble waiting" role="status" aria-live="polite">
        <span className="dot" />
        <span className="dot" />
        <span className="dot" />
        <span className="muted">{hint ?? '正在处理…'}</span>
      </div>
    </div>
  )
}
