/** 客服头像：软色圆底 + 戴耳麦的抽象人形。不放真人照片。 */

import { AGENT_PERSONA } from '../persona'

interface Props {
  size?: number
}

export default function Avatar({ size = 36 }: Props) {
  return (
    <span
      className="avatar-agent"
      style={{ width: size, height: size, background: AGENT_PERSONA.color }}
      aria-label={AGENT_PERSONA.title}
      role="img"
    >
      <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="9" r="3.4" />
        <path d="M5.5 20a6.5 6.5 0 0113 0" />
        <path d="M5 11a7 7 0 0114 0" />
        <path d="M5 11v3M19 11v3" />
        <path d="M16 15.5c0 1.4-1.2 2-2.5 2" />
      </svg>
    </span>
  )
}
