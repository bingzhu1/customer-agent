/** 按环境变量选客户端：`VITE_USE_MOCK=1` 走假数据，否则打真实后端。 */

import { createHttpClient } from './client'
import { createMockClient } from './mock'
import type { ApiClient } from './types'

const env = import.meta.env

export const USE_MOCK = env.VITE_USE_MOCK === '1'
export const API_BASE = env.VITE_API_BASE ?? 'http://localhost:8000'

/** 后端接口就绪情况：P1 交付后改 .env 即可，前端不用改代码。 */
const REAL_CAPABILITIES = {
  devToken: env.VITE_HAS_DEV_TOKEN === '1',
  confirm: env.VITE_HAS_CONFIRM === '1',
  getThread: env.VITE_HAS_GET_THREAD === '1',
}

export function createApiClient(getToken: () => string | null): ApiClient {
  return USE_MOCK
    ? createMockClient()
    : createHttpClient({ baseUrl: API_BASE, getToken, capabilities: REAL_CAPABILITIES })
}
