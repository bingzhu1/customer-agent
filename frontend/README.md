# frontend — 客服 Agent 对话页（demo 级）

演示一条主线：**LLM 负责理解与提议，确定性代码负责判定与执行**。
所以页面把"模型说的话"和"判定结果"分开渲染——回复文本归回复文本，
`decision` / `reason_code` / `confidence` / 引用的策略版本单独成区。

Vite + React + TypeScript，无组件库、无状态库、纯 CSS。

两个界面共用同一份会话状态，登录时选进哪个：

| 路由 | 给谁看 | 有什么 |
|---|---|---|
| `#/chat` 客户界面（默认） | 终端客户 | 单栏对话，"客服 Tracy 正在为您服务"，只有回复正文、说人话的退款确认卡、转人工提示、"查看依据的政策"。**不出现** reason_code / 置信 / 工具 / 用量 / request_id |
| `#/admin` 客服工作台 | 演示与排障 | 三栏，判定细节全展示。客户在 `#/chat` 聊的会话，这里能看到同一条的判定 |

客服人设（名字、头像底色、副标题）在 `src/persona.ts`，只改一处。

工作台三栏布局：左侧会话列表（本次会话里建过 / 打开过的 thread）· 中间对话流 · 右侧"本轮判定"面板
（decision / reason_code / 置信 / 引用 / 工具 / 用量）。对话流里只留回复正文、工具调用一行、
确认卡或说明卡、一行判定小字。视觉稿见 2026-09-05 的设计画布"客服 Agent 工作台"（方案 A）。

字体 IBM Plex Sans / Mono（Google Fonts，离线时回退系统字体与 PingFang SC）；语义色只有四组：
琥珀 = 待确认、红 = 拒绝、石板蓝 = 转人工、其余中性灰。

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
VITE_HAS_DEV_TOKEN=1   # 已交付（main a81d059）
VITE_HAS_CONFIRM=1     # 已交付（main 2cbaaa6）
VITE_HAS_GET_THREAD=1  # 已交付（main a81d059）
```

后端要以 `APP_ENV=dev` 启动，`/v1/dev/token` 才注册；CORS 默认放行 `http://localhost:5173`。

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

输入含「写路径」或「未开」的话，可以看到**动作没落库时**的形状：待确认卡片照常渲染
（金额与策略引用是真值），但 `action_id` / `confirm_url` / `expires_at` 为 null，确认按钮置灰。
mock 下点「确认执行」会给出执行回执；再点一次同一动作是幂等重放（replay），与真实后端一致。

左栏"按 thread_id 打开"填 `th_gone` 可以看到 §8.4 的 404 展示（"会话不存在或不属于你"）。

## 与后端契约对齐的几处（易踩）

| 处 | 实际 |
|---|---|
| 发消息请求体 | `{"message": "..."}`，后端 schema `extra="forbid"`，多一个字段就 400 |
| dev token 响应 | `{token, token_type, expires_in_minutes}` |
| `confidence` | 后端是 `"low"` / `"normal"`（PRD 示例写的是 high，类型两者都容纳） |
| `handoff_offer` | 是一句话**字符串**，不是对象 |
| `pending_action.summary.amount` | **字符串**（Decimal 序列化），前端不做数值换算 |
| `pending_action` 的 id 三件套 | 写路径已开（main 2cbaaa6），三者都是真值；落库失败时仍可能为 null，按 `action_id != null` 决定按钮能不能点 |
| `POST /v1/actions/{id}/confirm` 的响应 | **不是** §8.2 的对话响应，是执行回执 `{action_id, status, reason_code, replay, result, request_id}`，前端单独渲染成一条回执 |
| 历史消息 | 只有 `role` / `content` / `created_at`，**不回放判定结果** |

## 结构

```
src/
  api/types.ts    后端契约的唯一事实来源（§8.2 / §8.4 / §9.3 / §9.5 / §5.3）
                  —— 后端字段对不上时只改这一个文件
  api/client.ts   真实 HTTP：Bearer token、错误信封解析、AbortSignal 透传
  api/mock.ts     同接口的假数据，VITE_USE_MOCK=1 时替换
  api/errors.ts   §8.4 的展示口径（404 不区分"不存在"与"不属于你"）
  api/session.ts  token 只存内存，不落 localStorage
  timeline/reducer.ts   单条时间线的纯函数 (state, event) => state，无 DOM 依赖，有单测
  timeline/workspace.ts 多会话工作区：若干条时间线 + 激活态 + 侧栏摘要选择器，有单测
  timeline/*Item.tsx    四种时间线条目组件（AssistantFinalItem 含确认卡 / 说明卡）
  timeline/decision.ts  六种 decision 的文案、色调、图标表
  components/Sidebar.tsx        左栏会话列表 + 按 id 打开 + 身份
  components/JudgmentPanel.tsx  右栏本轮判定（原 DebugDrawer）
  components/Icon.tsx           线性 SVG 图标，不用 emoji
  components/Avatar.tsx         客服头像（抽象人形 + 耳麦）
  persona.ts            客服人设常量
  useWorkspace.ts       副作用层：建会话 / 发消息 / 确认 / 拉历史 / 切换；在 App 层调一次，两个界面共用
  pages/CustomerChat.tsx  #/chat 客户界面
  pages/Chat.tsx          #/admin 客服工作台
  pages/Login.tsx pages/Review.tsx
```

会话列表现在只有本次会话里建过或打开过的 thread；后端 `GET /v1/threads`（FR-109，PRD v1.5）
交付后，在 `thread.open` 时把服务端条目灌进 `workspace` 即可，组件不用改。

`reducer.ts` 现在只认 `user.message` / `waiting` / `assistant.final` / `error` 四种事件。
Phase 4 接 SSE 时加 `token` / `tool_started` 等事件，**state 与条目形状不变**。

## 检查

```bash
npm run test    # vitest：reducer 单测（六种 decision + 错误）+ workspace 单测（多会话）
npm run lint    # eslint
npm run build   # tsc -b && vite build

# 对真实后端的联调冒烟（默认跳过；要先在仓库根 APP_ENV=dev 起服务）
VITE_INTEGRATION=1 npm run test        # 只读，不写库
VITE_INTEGRATION_WRITE=1 npm run test  # 额外跑确认退款，会真的写 biz.refunds
```

**写用例为什么默认关**：demo 与 eval 共用同一个库，确认退款会被评估的副作用探针
记成用例副作用（over-refund 指标被污染）。评估在跑时不要开这个开关，也不要在
真实后端上反复点确认。

联调测试会真的调模型（一轮约 10–35 秒），断言只挑契约层面的点：
82913 → `REQUIRE_CONFIRMATION` / `POLICY_SATISFIED`、`amount="89.00"`、可点的 `action_id`；
确认后回执带 `simulated=true`，再确认一次是 `replay=true` 的幂等重放；404 信封被解析成 `ApiError`。

## 不做

主题切换、富文本输入、服务端会话列表（等 FR-109）、嵌入组件、管理后台、流式渲染。
`#/review` 是 Phase 6 M6 的审批页，现在只有占位。
