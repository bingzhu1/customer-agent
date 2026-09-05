# PLAN — 开发 checklist（跨 Phase、跨 session 的唯一地图）

> 三份文档的分工：**PLAN** 是地图（做什么、谁做、做完没），**HANDOFF** 是当前快照（接手用），**PROGRESS** 是流水（发生过什么）。
> 规则：① milestone 完成必须勾掉并填日期 + commit；② **计划里没有的工作，先加进这里再动手**；③ 每个 Phase 的 DoD 全勾才算完成，合 `main` 打 tag。
> 依据：PRD §15 路线图、§12.6 演进表、CLAUDE.md §11 DoD 模板。

图例：`[x]` 完成 · `[~]` 进行中 · `[ ]` 未开始 · 负责：master / P1 / P3 …（session 名）

---

## ⚡ 3 小时冲刺（2026-09-05 晚，用户拍板）：V1 + V3 核心闭环

目标：用最短路径证明核心命题"LLM 提议、确定性代码判定"。产出 V0 → V1 → V3 三行演进表，
authorization violation 7 → 0，越权 / 超期 / 食品 / 定制退款被确定性拒绝并引用正确策略。

| 时段 | Phase 1（P1） | Phase 3（P3） | master |
|---|---|---|---|
| 0–60 分 | [x] 已合入 main（两个已验收 milestone）；[x] LangGraph 最小图 + 4 个只读工具 + policy_gate / decide 节点接 P3 `87b98e0`，已合 main `6667cca` | [x] 引擎 + 矩阵已交付 `e2a5927` → [ ] rebase main；[x] 受约束的拒绝 / 升级话术模板 `decision/templates.py` `add05c9`，已合 main `d108a05` | [x] 合 main、tag；[x] 审 P3 接口定稿（PolicyFacts 8 字段 / evaluate / decide，与 prompt 一致）；[ ] PLAN 改冲刺版 |
| 60–120 分 | [x] `agents/v1_tools.py` 实现 `AgentUnderTest`，`make eval AGENT=v1` —— V1 19/54，硬门槛全绿，authorization violation = 0 | [ ] 交付模板；待命修 bug | [x] 合 P3 到 main `d31d9a7`；[ ] 跑 V1 eval，把授权用例修到 0 违规 |
| 120–180 分 | [ ] `policy_gate` / `decide` 节点接 P3 的 `evaluate` / `decide`，`agents/v3_policy.py`，`make eval AGENT=v3` | [ ] 待命 | [ ] 跑 V3 eval；[ ] README 写 V0→V3 演进表；[ ] 合 P1 到 main，tag `v0.4-sprint` |

**冲刺期间明确推后**（不删，回到各 Phase 的正常节奏再做）：RAG 与向量检索（`search_policy` 用策略 `human_text` 关键词匹配代替，标注"非真 RAG"）、
写路径与幂等（停在 REQUIRE_CONFIRMATION，不执行）、Postgres checkpointer、SSE、Langfuse、限流 / 超时中间件、
prompt caching、记忆压缩与三方对比、人工控制台、混沌测试。

**冲刺不放松的**：三条红线；安全类指标仍是硬门槛；每次合并前 `make test && make lint`；不改测试来让测试过。

---

## Phase 0 — 评估先行的地基 · 分支 `phase0-eval-foundation` · PR #1

- [x] M1 环境与骨架：uv / colima / docker-compose(pg+pgvector+Langfuse) / Makefile / Settings —— master · 2026-09-05 · `d61b2f0`
- [x] M2 数据产物：夹具契约、biz 7 表 + Alembic 0001 + seed、11 条策略 YAML、54 条 golden、schema 加固 —— master + 3 subagent · 2026-09-05 · `4f8bcf5`
- [x] M3 eval runner：断言引擎、副作用探针、跨轮特判、§12.4 指标、报表、落库、judge、`make eval` —— master · 2026-09-05 · `8cc6365`
- [x] 接口锁定 `eval/protocol.py`（AgentUnderTest / TurnResult）—— master · `ba08ac8`
- [x] V0 naive baseline 实现 —— session 2 · 2026-09-05 · `4402695`
- [x] V0 全量实测 + `docs/eval/v0-baseline.md` + PRD §12.6 填值 —— master · 2026-09-05 · `ecdb6bd`

**DoD（PRD §15）**
- [x] `make eval` 一条命令产出 V0 全指标表
- [x] 报表进版本库（`eval_reports/latest_v0-naive.md`）
- [x] 能明确看到裸 LLM 错在哪里（`docs/eval/v0-baseline.md`）
- [x] PR #1 合入 `main`，tag `v0.1-phase0` —— 2026-09-05 · `08ae677`

---

## Phase 1 — 生产骨架 + 只读工具 · 分支 `phase1-skeleton` · 负责 P1

- [x] M1 Alembic 0002 agent 平台表 + AuthContext + Repository 强制 scope（FR-802/803/804）· `afd4107`
- [x] M2 FastAPI 骨架 + JWT（FR-801）+ `/health` `/ready` `/metrics` `/v1/whoami` + structlog + prometheus · `089e467`
- [~] rebase 到 `origin/phase0-eval-foundation`，解 settings / test_migrations / uv.lock 三处冲突
- [ ] M3 `POST /v1/threads`、`GET /v1/threads/{id}`（他人 404，FR-101/104）
- [~] M4 LangGraph 最小图 ingest→understand→act→policy_gate→decide→respond（`87b98e0`，MemorySaver）；Postgres checkpointer 推后
  > 备注（2026-09-05）：Postgres checkpointer 可参考 embedease-ai `backend/app/core/db/checkpointer.py` 的 Provider 形状——`AsyncPostgresSaver` + `psycopg_pool` 懒加载、SQLite / Postgres 可切、单例 + `close()`。该仓库无 LICENSE，只借鉴设计，不复制代码。
- [x] M5 4 个只读工具，签名无身份字段（FR-208），不可信包装（FR-209），单轮预算（FR-210）· `87b98e0`；search_policy 为关键词占位，Phase 2 替换
- [ ] M6 中间件：request_id / 限流 429（FR-806）/ 超时；Langfuse trace（FR-902）；prompt caching（FR-911）；分节点 effort（FR-912）
- [x] M7 实现 `AgentUnderTest` 接线 → `make eval AGENT=v1`，报表入库，对照 V0 · 2026-09-05 · `efb8082`
- [ ] 与 Phase 3 接线：`policy_gate` / `decide` 节点调用 P3 的纯函数（Phase 3 交付后）

**DoD**
- [ ] V1 评估跑通，**authorization violation = 0**
- [ ] kill 进程后同一 `thread_id` 能续上
- [ ] Langfuse 中可见完整 trace
- [ ] 合 `main`，tag `v0.2-phase1`

---

## Phase 2 — RAG + 策略事实来源 · 分支 `phase2-rag` · 负责 P2 · provider 已定：OpenAI text-embedding-3-small

- [x] M1 YAML → chunk 生成器（FR-301）· `0180ce7`，已合 main `2ed4e30`
- [x] M2 OpenAI embeddings + FakeEmbeddings + 迁移 0003 pgvector/HNSW + ingest · `0180ce7`
- [~] M3 PolicyRetriever（top-k + τ 门控 band）已交付；`search_policy` 工具换用它 —— P1 冲刺后接；查询改写（FR-302）未做
- [~] M4 引用后置校验纯函数 `rag/citations.py` 已交付；接进 respond 节点 —— P1；低置信模板已在 decision/templates（P3）
- [~] M5 `scripts/calibrate_tau.py` 已交付；真跑标定 + 回填 ADR-0007 —— P2；`protocol.TurnResult.retrieved` —— master
- [ ] M6 `make eval AGENT=v2`

**DoD**
- [ ] citation correctness ≥ 95%；无结果场景 100% 不编造；τ 标定过程在 ADR-0007
- [ ] 合 `main`，tag `v0.3-phase2`

---

## Phase 3 — 确定性策略引擎 + 决策层 · 分支 `phase3-policy-engine` · 负责 P3（可与 Phase 1 并行）

- [x] M1 `PolicyFacts` + `evaluate()` 策略引擎（`e2a5927`，待 master 审）（纯函数，FR-401/402）+ 契约 16 订单参数化 + 边界用例（FR-408）
- [x] M2 `DecisionInput` + `decide()` §9.4（`e2a5927`，待 master 审） 有序矩阵（FR-404/405/406）+ 优先级测试
- [x] M3 master 审 `PolicyFacts` / `DecisionInput` 接口并定稿；ADR-0010 `b01b57c`
- [x] M4 受约束话术骨架（拒绝 / 升级模板，LLM 只填变量，FR-407）· `add05c9`
- [ ] M5 接入图（`policy_gate` / `decide` 节点，事实从 biz 实时查，FR-403）→ `make eval AGENT=v3`

**DoD**
- [ ] policy correctness ≥ 95%；citation-execution consistency = 100%
- [ ] 策略相关测试全部不依赖 LLM
- [ ] 合 `main`，tag `v0.4-phase3`

---

## Phase 4 — 写路径：提议 → 确认 → 执行 · 内核先行 · 负责 P3（内核：actions/ services/）+ P1（interrupt / resume / confirm API）

- [~] M1 `ActionProposal` + 幂等键 + 状态机 + ActionService（FR-501~505）—— P3，2026-09-05 开工，分支仍用 `phase3-policy-engine`
- [ ] M2 interrupt / resume（FR-601/602）+ `POST /v1/actions/{id}/confirm`
- [~] M3 `RefundService(SIMULATED)`（FR-506）+ `audit_log` 追加式（FR-507）+ 重试幂等（FR-508）—— P3，与 M1 一起
- [ ] M4 `escalate_to_human` 创建 human_review 并中断（FR-206）；`create_ticket`（FR-205，P1）
- [ ] M5 SSE 事件协议 v1（FR-103，§8.3）
  > 备注（2026-09-05）：事件命名与 payload 可参考 embedease-ai `backend/app/schemas/events.py`——按命名空间分层（`meta.start` / `tool.start` / `tool.end` / `assistant.final` / `model.fallback`），每个事件配 TypedDict payload；前端消费侧参考其 `frontend/packages/chat-sdk/src/timeline/reducer.ts`（纯 reducer 把事件流折成时间线，可脱离浏览器测试）。我方 §8.3 的 `requires_confirmation` / `requires_human` 是终结事件，这一点保留。同样只借鉴设计。
- [ ] M6 outbox 升级点写入 PRD §17（FR-509）；`make eval AGENT=v4`
- [ ] M7 前端对话页（demo 级）：`web/` 目录 Vite + React + TS，无组件库；fetch 读 SSE + AbortController，纯 `timelineReducer` 折事件为时间线并单测；页面显式渲染 decision / reason_code / 引用 / 确认按钮（打 `POST /v1/actions/{id}/confirm`）。不做：登录页（token 粘贴框即可）、主题、富文本、历史会话列表

**DoD**
- [ ] 重复退款 = 0（IDEM-001/002 通过）；审计日志能回答"谁、何时、依据哪条规则、结果如何"
- [ ] 合 `main`，tag `v0.5-phase4`

---

## Phase 5 — 记忆：CaseFacts + 压缩 + 长期记忆 · 未开始

- [ ] M1 强类型 `CaseFacts`，只由确定性代码写入（FR-701/702），`case_state` 物化
- [ ] M2 token 阈值触发叙述压缩，CaseFacts / pending_action 不参与（FR-703）
- [ ] M3 `user_memory` 异步抽取（置信度 / TTL / 来源 / 版本，FR-704/705）+ 查询 / 删除（FR-706）+ 注入标注非权威（FR-708）
- [ ] M4 记忆投毒测试进 CI（FR-707，红线 3）
- [ ] M5 三方对比实验 A/B/C（§12.5），图表进 README；`make eval AGENT=v5`

**DoD**
- [ ] entity retention ≥ 90%；投毒测试全过；三方对比如实记录（不成立也要写）
- [ ] 合 `main`，tag `v0.6-phase5`

---

## Phase 6 — 人工控制台 + 韧性 + 可观测 · 未开始

- [ ] M1 `/v1/human-review` 队列 + approve / edit / reject 从同一 checkpoint 恢复（FR-603/604/605/606）
- [ ] M2 超时 / 重试 / 模型降级 / 熔断 / 优雅降级（FR-908/909）；优雅关闭（FR-107）
- [ ] M3 Prometheus + Grafana 面板（FR-903/910）；成本与延迟 SLO；`Usage` 改为逐次调用列表以按模型拆成本
- [ ] M4 故障注入测试（关 pgvector / LLM 超时 / 连续失败）
- [ ] M5 `make eval AGENT=v6` 全表；README 含 V0→V6 演进表、架构图、"真正上线还缺什么"
- [ ] M6 前端审批页（demo 级）：复用 `web/`，一张 human-review 队列表 + approve / edit / reject 三个按钮，操作后回到对话页看恢复结果。不做：热度排序、通知、多客服在线状态

**DoD**
- [ ] 混沌测试通过；$0.05/session 成本门槛实测（不达标则修订目标并说明原因）
- [ ] 合 `main`，tag `v1.0`

---

## 跨 Phase 的未决与待补

- [ ] 未决 1：embedding provider（OpenAI text-embedding-3-small vs Voyage）—— Phase 2 前用户拍板
- [ ] 未决 3：τ_low / τ_high 实测值 —— Phase 2 标定后回填 ADR-0007
- [ ] `protocol.TurnResult.retrieved` 字段 —— Phase 2 前
- [ ] `protocol.Usage` 逐次调用列表 —— Phase 6 前
- [ ] `tests/test_api_auth.py` 不应依赖 `.env` 的 `JWT_SECRET`，应在 fixture 里覆盖 settings —— P1 顺手修
- [x] 前端范围 —— 用户 2026-09-05 拍板：正式页面但 demo 级、Vite + React、对话页进 Phase 4 M7、审批页进 Phase 6 M6；PRD §2 "仅最小调试页" 待改 v1.2。可借鉴 embedease-ai `frontend/packages/chat-sdk`（fetch 流 + AbortController + 纯 timelineReducer）与 `frontend/app/support` 客服工作台（热度排序、接管）的设计；其管理后台 / 嵌入组件 / 商品卡片与我方无关
- [ ] Phase 0–2 单窗口规则（CLAUDE.md §9.1）已被"文件不相交 + 接口先锁"的方式放开，是否改写 CLAUDE.md —— 用户决定

## 偏离记录（计划外的事，先记后做）

- 2026-09-05：前端从 PRD "最小调试页" 扩为正式 demo 页面（Phase 4 M7 / Phase 6 M6），用户拍板；PRD §2 与 §15 待同步。

| 日期 | 事项 | 决定 |
|---|---|---|
| 2026-09-05 | Phase 1 加 `/v1/whoami`（PRD §8.1 原无） | 保留，PRD v1.1 |
| 2026-09-05 | Phase 0 期间开三 session 并行（违反 §9.1 单窗口） | 用户决定放开，条件：接口先锁 + 文件不相交 + 独立数据库 |
