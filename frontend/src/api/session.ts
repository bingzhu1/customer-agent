/**
 * 内存中的 token。放模块作用域而不是 React state / ref：
 * 它不参与渲染，client 也要能在任意时刻同步读到最新值。
 * 不落 localStorage——demo 刷新即需重新登录，也就不会有长期有效 token 躺在浏览器里。
 */

let token: string | null = null

export function getToken(): string | null {
  return token
}

export function setToken(value: string | null): void {
  token = value
}
