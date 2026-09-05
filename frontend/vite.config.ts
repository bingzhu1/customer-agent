import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 前端不直连数据库，只打后端 REST（默认 http://localhost:8000，见 .env.example）。
export default defineConfig({
  plugins: [react()],
  // host: true 同时监听 IPv4 与 IPv6——只绑 ::1 时，浏览器把 localhost 解析到 127.0.0.1 会连不上；
  // strictPort 让端口被占时直接报错，而不是悄悄挪到 5174。
  server: { port: 5173, host: true, strictPort: true },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
