/** 极简 hash 路由：只有 /chat 与 /review 两个页面，不值得引路由库。 */

import { useEffect, useState } from 'react'

function current(): string {
  return window.location.hash.replace(/^#/, '') || '/chat'
}

export function useHashRoute(): string {
  const [route, setRoute] = useState(current)
  useEffect(() => {
    const onChange = () => setRoute(current())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
