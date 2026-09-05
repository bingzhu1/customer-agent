# frontend — 客服 Agent 对话页（demo 级）

演示一条主线：**LLM 负责理解与提议，确定性代码负责判定与执行**。
所以页面把"模型说的话"和"判定结果"分开渲染——回复文本归回复文本，
`decision` / `reason_code` / `confidence` / 引用的策略版本单独成区。

Vite + React + TypeScript，无组件库、无状态库、纯 CSS。

## 跑起来

```bash
cd /Users/bingzhu/Desktop/ca-frontend/frontend
npm install
cp .env.example .env     # 默认 VITE_USE_MOCK=1
npm run dev              # http://localhost:5173
```

### 两种数据源

| 模式 | `.env` | 说明 |
|---|---|---|
| mock（默认） | `VITE_USE_MOCK=1` | 不需要后端。登录随便填，六种 decision 各有一条样本 |
| 真实后端 | `VITE_USE_MOCK=0` + `VITE_API_BASE=http://localhost:8000` | 另开终端在仓库根跑 `make serve` |

真实后端下，接口交付情况由 `.env` 里三个开关控制，**后端就绪后改开关即可，前端不用改代码**：

```
VITE_HAS_DEV_TOKEN=0   # POST /v1/dev/token 就绪后改 1
VITE_HAS_CONFIRM=0     # POST /v1/actions/{id}/confirm 就绪后改 1
VITE_HAS_GET_THREAD=0  # GET  /v1/threads/{id} 就绪后改 1
```

未就绪的按钮置灰并在界面上注明原因，而不是点下去报错。
`/v1/dev/token` 未就绪时，用仓库根的 `make token USER=101` 签一个粘进登录页即可。

### mock 下的演示路径

| 输入 | 走到 |
|---|---|
| 我要退款 | `REQUIRE_CONFIRMATION` — 89 元退款明细 + 确认 / 取消 |
| 620 元那笔怎么退 | `REQUIRE_HUMAN` — 超自动限额，已转人工 |
| 99999 是别人的订单 | `DENY` — `OWNERSHIP_MISMATCH`，不暴露存在性 |
| 物流到哪了 | `DEGRADE` — 部分信息暂不可用 |
| 查一下我的订单 | `REQUEST_INFO` — 索取订单号 |
| 这个不确定吧 | `ANSWER` + `confidence=low` — 低置信标记 |
| 其他 | `ANSWER` — 正常回答带引用 |

历史输入框填 `th_gone` 可以看到 §8.4 的 404 展示（"会话不存在或不属于你"）。

## 结构

```
src/
  api/types.ts    后端契约的唯一事实来源（§8.2 / §8.4 / §9.3 / §9.5 / §5.3）
                  —— 后端字段对不上时只改这一个文件
  api/client.ts   真实 HTTP：Bearer token、错误信封解析、AbortSignal 透传
  api/mock.ts     同接口的假数据，VITE_USE_MOCK=1 时替换
  api/errors.ts   §8.4 的展示口径（404 不区分"不存在"与"不属于你"）
  api/session.ts  token 只存内存，不落 localStorage
  timeline/reducer.ts   纯函数 (state, event) => state，无 DOM 依赖，有单测
  timeline/*Item.tsx    四种时间线条目组件
  timeline/decision.ts  六种 decision 的文案与配色表
  pages/Login.tsx pages/Chat.tsx pages/Review.tsx
  DebugDrawer.tsx 本轮 tools_used / usage / latency_ms / request_id
```

`reducer.ts` 现在只认 `user.message` / `waiting` / `assistant.final` / `error` 四种事件。
Phase 4 接 SSE 时加 `token` / `tool_started` 等事件，**state 与条目形状不变**。

## 检查

```bash
npm run test    # vitest：reducer 单测，六种 decision 各一条 + 错误
npm run lint    # eslint
npm run build   # tsc -b && vite build
```

## 不做

主题切换、富文本输入、历史会话列表、嵌入组件、管理后台、流式渲染。
`#/review` 是 Phase 6 M6 的审批页，现在只有占位。
