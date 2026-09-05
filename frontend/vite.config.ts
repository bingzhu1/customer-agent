import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 前端不直连数据库，只打后端 REST（默认 http://localhost:8000，见 .env.example）。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
