/** 线性图标，16/24 网格，全部 stroke，随 currentColor 变色。不用 emoji。 */

const PATHS = {
  check: 'M5 12l5 5L20 7',
  x: 'M18 6L6 18M6 6l12 12',
  plus: 'M12 5v14M5 12h14',
  'arrow-up': 'M12 19V5M5 12l7-7 7 7',
  alert: 'M12 9v4M12 17h.01M10.3 3.9L2.6 17.3A2 2 0 004.3 20h15.4a2 2 0 001.7-2.7L13.7 3.9a2 2 0 00-3.4 0z',
  info: 'M12 8v5M12 16h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  user: 'M16 8a4 4 0 11-8 0 4 4 0 018 0zM4 21a8 8 0 0116 0',
  ban: 'M21 12a9 9 0 11-18 0 9 9 0 0118 0zM5.6 5.6l12.8 12.8',
  doc: 'M6 3h9l4 4v14H6zM14 3v5h5',
  external: 'M7 17L17 7M8 7h9v9',
  chat: 'M4 5h16v11H9l-5 4z',
  panel: 'M4 5h16v14H4zM15 5v14',
  logout: 'M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9',
  clock: 'M12 7v5l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
} as const

export type IconName = keyof typeof PATHS

interface Props {
  name: IconName
  size?: number
  className?: string
  strokeWidth?: number
}

export default function Icon({ name, size = 16, className, strokeWidth = 2 }: Props) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}

/** 进行中的小圆环，纯 CSS 动画 */
export function Spinner({ size = 14 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden="true" />
}
