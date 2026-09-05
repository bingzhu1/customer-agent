
> 追加式记录，永不删除历史。每次有实质行动追加一行。
> 当前状态快照见 [`HANDOFF.md`](HANDOFF.md)。

| 日期 | Phase | 做了什么 | commit | 验证状态 |
|---|---|---|---|---|
| 2026-09-05 | 前期 | 架构讨论定稿：单 Agent、双 schema、LLM 提议/确定性执行、记忆五层、低置信带策略 | — | 用户已确认 |
| 2026-09-05 | 前期 | 创建 GitHub 仓库 `bingzhu1/customer-agent`（private），本地 git init | — | 已推送 |
| 2026-09-05 | 前期 | 写 `docs/PRD.md` v1.0（18 章，1270 行） | `e1ce768` | 待用户 review |
| 2026-09-05 | 前期 | 写 `CLAUDE.md` 工作规则 + 9 篇 ADR + PROGRESS/HANDOFF | `3ce3705` | 待用户 review |
| 2026-09-05 | 前期 | 定主模型 Claude Sonnet 5、仓库转 public；PRD 增补 §13.4 模型配置与成本口径（含成本超标结论）、FR-911/912 | `25e5fc4` | 待用户 review |
| 2026-09-05 | 前期 | 记录环境问题（本 session 无法切到 `claude-fable-5-1`），更新 HANDOFF 交接，转到新 session 继续 | `a48af8c` | — |
| 2026-09-05 | Phase 0 | 用户拍板：成本目标 $0.05 维持；授权安装工具。装 uv/docker CLI/docker-compose/colima；开分支 `phase0-eval-foundation`；写 pyproject(uv)、docker-compose(pg+pgvector+Langfuse v3)、.env.example、Makefile、Settings 与首个单测 | `d61b2f0` | test 3/3、lint 通过；pg 双 schema + pgvector 验证通过；Langfuse 3.225.7 健康（宿主端口 3001） |
| 2026-09-05 | Phase 0 | 共享地基：领域枚举、策略 YAML schema、golden 用例 schema、夹具契约 `docs/phase0-fixtures.md` | `9e89864` | test 12/12、lint 通过 |
| 2026-09-05 | Phase 0 | milestone 2（三路 subagent 并行）：SQLAlchemy 模型 + Alembic 初始迁移 + 可复现 seed（20 用户 / 60 单）；11 条策略 YAML；54 条 golden dataset；跨产物一致性测试；按反馈加固两个 schema（any_of、引用非空、informational 禁字段、anchor 与 domain 一致等） | `4f8bcf5` | test 95/95、lint + mypy strict 通过；migrate + seed 端到端通过 |
| 2026-09-05 | Phase 0 | 锁定被测接口 `eval/protocol.py`（AgentUnderTest / TurnResult），HANDOFF 登记三 session 并行分工 | `ba08ac8` | test 97/97 |
| 2026-09-05 | Phase 0 | milestone 3：eval runner —— 断言引擎、副作用探针（查库不信自述）、跨轮特判（幂等恰好一次 / 存在性模板一致）、§12.4 指标、markdown+JSON 报表、eval_runs 落库、LLM judge（可选）、哑 agent、`make eval` | `8cc6365` | test 117/117、lint + mypy 通过；`make eval AGENT=dummy` 1/54、硬门槛 FAIL（预期） |
| 2026-09-05 | Phase 0 | 修复：迁移往返测试改用一次性库 `cs_agent_test`，不再清空开发库的 eval_runs 与 seed；alembic env 支持 Config 覆盖连接串 | `c506733` | test 117/117；开发库 eval_runs 保留 |
| 2026-09-05 | Phase 0 | 三 session 进度对齐（master 迁到终端后可直接互发消息）；用户拍板：等 V0 交付后一并合入；`/v1/whoami` 进 PRD §8.1（v1.1）；protocol 文档补并发约定与两项待补字段 | `f1f5390` | — |
| 2026-09-05 | Phase 0 | 合入 V0 naive baseline（session 2 交付 `4402695`）；全量实测 1/54、硬门槛 FAIL、$0.011/session；写 `docs/eval/v0-baseline.md`，PRD §12.6 V0 行填实测（v1.2）。首跑 5 条网络异常已重跑 | `590fcce` | test 134/134；eval_run_id 4 |
| 2026-09-05 | 冲刺 | PR #1 合入 main，tag `v0.1-phase0`；合入 Phase 3 引擎 + 矩阵（`e2a5927`）；修迁移测试库跨 worktree 污染（库名带路径哈希 + 每次重建）；PLAN 增加 3 小时冲刺分工 | `7d16210` | test 218/218、lint 通过 |
| 2026-09-05 | Phase 1 | milestone 1：迁移 0002 建 PRD §7.3 其余 10 张 agent 表（向量列 Text 占位，Phase 2 换 pgvector），`agent_actions` 上 UNIQUE(idempotency_key)、`policy_chunks` 上版本唯一键；`AuthContext(user_id, roles)` 与 `BizRepository`（get_order / get_shipping / get_ticket 强制 `WHERE user_id = ctx.user_id`）；授权与迁移往返测试。独立库 `cs_agent_p1` | `6eb8984` | test 113/113、lint + mypy strict 通过 |
| 2026-09-05 | Phase 1 | milestone 2：FastAPI 骨架 —— `/health`（不依赖外部）/`/ready`（探 DB）/`/metrics`（Prometheus 文本）、`/v1` 前缀、JWT 中间件产出 `AuthContext`（HS256，固定 algorithms 防 alg 混淆）、§8.4 统一错误信封、request_id 中间件 + structlog；新依赖 fastapi/uvicorn/pyjwt/prometheus-client/httpx(dev) 已获确认 | `4942f73` | test 143/143、lint + mypy strict 通过；本机起服务手工验证 health/ready/whoami/metrics 全通 |
| 2026-09-05 | Phase 1 | 把 `phase1-skeleton` rebase 到 Phase 0 tip（`4402695`，含 V0 baseline）：settings 两边新增行都留；test_migrations 以 phase0 的一次性库 `cs_agent_test` 版为准再补 agent 表断言与幂等键用例；Makefile 保留双方目标；uv.lock 由 `uv sync` 重生成 | `306ae10` | test 182/182、lint + mypy 通过；`make eval AGENT=dummy` 仍是 1/54（与 Phase 0 基线一致）|
| 2026-09-05 | Phase 1 | milestone 1：迁移 0002 建 PRD §7.3 其余 10 张 agent 表（向量列 Text 占位，Phase 2 换 pgvector），`agent_actions` 上 UNIQUE(idempotency_key)、`policy_chunks` 上版本唯一键；`AuthContext(user_id, roles)` 与 `BizRepository`（get_order / get_shipping / get_ticket 强制 `WHERE user_id = ctx.user_id`）；授权与迁移往返测试。独立库 `cs_agent_p1` | `dfc374d` | test 113/113、lint + mypy strict 通过 |
| 2026-09-05 | Phase 1 | milestone 2：FastAPI 骨架 —— `/health`（不依赖外部）/`/ready`（探 DB）/`/metrics`（Prometheus 文本）、`/v1` 前缀、JWT 中间件产出 `AuthContext`（HS256，固定 algorithms 防 alg 混淆）、§8.4 统一错误信封、request_id 中间件 + structlog；新依赖 fastapi/uvicorn/pyjwt/prometheus-client/httpx(dev) 已获确认 | `1d9f7ca` | test 143/143、lint + mypy strict 通过；本机起服务手工验证 health/ready/whoami/metrics 全通 |
| 2026-09-05 | Phase 1 | 把 `phase1-skeleton` rebase 到 Phase 0 tip（`4402695`，含 V0 baseline）：settings 两边新增行都留；test_migrations 以 phase0 的一次性库 `cs_agent_test` 版为准再补 agent 表断言与幂等键用例；Makefile 保留双方目标；uv.lock 由 `uv sync` 重生成 | `e39e400` | test 182/182、lint + mypy 通过；`make eval AGENT=dummy` 仍是 1/54（与 Phase 0 基线一致）|
| 2026-09-05 | Phase 1 | rebase 到 `origin/main`（已含 Phase 0 + Phase 3 策略引擎/决策矩阵）：test_migrations 以 main 版为准再补 agent 表断言；文档冲突两边都留后去重 | `f53a282` | test 266/266 通过 |
| 2026-09-05 | 跨 Phase | 评估参考仓库 congwa/embedease-ai：技术栈相近但无策略引擎 / 授权 scope / eval；在 PLAN 的 Phase 1 M4（checkpointer Provider）、Phase 4 M5（SSE 事件分层命名 + 前端 timelineReducer）加备注，前端范围记入未决待拍板。该仓库无 LICENSE，只借鉴设计 | `61b65ad` | 文档改动，无测试 |
| 2026-09-05 | 跨 Phase | 用户拍板前端：demo 级正式页面，Vite + React；PLAN 加 Phase 4 M7 对话页、Phase 6 M6 审批页，未决勾掉，HANDOFF 记已定 | 见 git log | 文档改动 |
| 2026-09-05 | Phase 1 | 冲刺第二段：`agents/v1_tools.py` 实现 `AgentUnderTest`（name=`v1-tools`），registry 加 `v1`；`make eval AGENT=v1` 全量实测 | `efb8082` | test 412/412、lint 通过；V1 **19/54**（V0 为 1/54），**安全硬门槛全绿**：authorization violation 0、over-refund 0、injection resistance 100% |
| 2026-09-05 | 冲刺 | 合入 Phase 2 首批（RAG 零件）与 Phase 1 V1；main 复跑 V1：17/54、authorization violation 1（SEC-008 冒充主管走了 LLM 措辞拒绝而非矩阵规则 3）、SEC-004 回复泄露内部规则编号——已交 Phase 1 修；修 runner 单轮存在性检查误判 | `0a52e47` | test 466/466；V1 硬门槛 FAIL（待修） |
| 2026-09-05 | Phase 1 | 冲刺第三段：`agents/v3_policy.py`（打开 policy_gate）+ registry 加 `v3`；respond 接 `decision/templates` 逐字输出（非 ANSWER 不调 LLM）；understand 加 `claims_elevated_role` / `references_other_user` 两个越权信号 + 不依赖 LLM 的 `user \d+` 正则兜底 | `f54962b` | test 481/481、lint 通过；V3 **25/54**（V1 19/54、V0 1/54），硬门槛全绿，security **10/10**，citation-execution consistency 100%；V1 复跑同样修复 SEC-004/008/010 |
| 2026-09-05 | 冲刺 | 合入 V3（25/54，硬门槛全绿，security 10/10，一致性 12/12）、Phase 5 记忆确定性部分、前端三段；README 增 V0→V1→V3 演进表；PRD v1.3 填 V1/V3 实测 | `dc40bc6` | test 554/554 |
| 2026-09-05 | Phase 1 | 第二轮 ②：`POST /v1/threads`、`POST /v1/threads/{id}/messages`（§8.2 响应体）、`GET /v1/threads/{id}`、dev-only `POST /v1/dev/token`；新增 `repositories/agent.py`（会话按 user 收口）与 `services/chat.py`（事务边界）；顺手修掉 main 里 `latest_v1-tools.md` 残留的合并冲突标记 | `5c0400e` | test 564/564、lint 通过 |
| 2026-09-05 | 冲刺 | 前端四段合入（登录 / 对话 / 六种 decision / 确认按钮 / 调试抽屉 / 历史，mock 优先，npm test 11、lint、build 全过，FE session 交付）；Phase 1 ② 四个聊天接口合入；Phase 4 内核任务下发 P3；PRD v1.4 矩阵加规则 6b | `4c3e547` | test 564/564 |
| 2026-09-05 | 跨 Phase | 前端评估：现有前端够用、不重构；assistant-ui 仅参考、shadcn 不引入。发现会话侧栏无后端接口，PRD v1.5 加 `GET /v1/threads`（FR-109），PLAN Phase 4 M7 加依赖 | 见 git log | 文档改动 |
| 2026-09-05 | 冲刺 | 合入 Phase 1 前端对接（CORS / pending_action / dev token）、Phase 2 查询改写与 ADR-0007 τ 标定、前端契约对齐；主目录 8000 起 main 版后端供前端联调（误停 P1 的 8123 服务，已告知）；策略 chunk 入库 44 行 | `f77e5ba` | test 591/591（全量耗时升至 2 分钟，待查） |
| 2026-09-05 | 冲刺 | 合入 Phase 4 内核（P3：幂等键 / 状态机 / ActionService / RefundService / 审计，矩阵规则 6b）；前端联调三条全过并合入冒烟测试；HANDOFF 分工表补 Phase 4 / 记忆 / 前端三行 | `7770eb8` | test 730/730 |
| 2026-09-05 | Phase 1 | 第二轮 ③：`search_policy` 换 `rag.retriever.PolicyRetriever`（真向量检索 + `rag.rewrite.fallback_query` 查询改写 + max_score 接进矩阵 τ 门控）；新增 `rag/provider.py` 显式选 provider（`EMBEDDING_PROVIDER`，默认 fake）并按 provider 标定 τ；citations 的 anchor 兜底从 PolicySet 取 | `5847400` | test 597/597、lint 通过；V3 重跑 25/54，硬门槛全绿（**本次报表用 fake provider，τ=0.28/0.40**） |
| 2026-09-05 | Phase 4 M7 | 前端视觉改版方案 A：三栏布局（会话侧栏 / 对话流 / 本轮判定面板），新增 `timeline/workspace.ts` 多会话状态与 `useWorkspace` 副作用层，`components/{Sidebar,JudgmentPanel,Icon}`，重写 styles.css 与 AssistantFinalItem；api 层零改动 | 见 git log（frontend-chat） | tsc / lint / vitest 17 通过 / build 通过；mock 下六种 decision、确认流程、新会话与切换在浏览器人工走通 |
| 2026-09-05 | Phase 1 | 第二轮 ⑤：记忆接线——act 用 apply_tool_result 填 CaseFacts、新增 persist 节点用 apply_verdict 记判定依据并落 agent.case_state；understand 后用 CaseFacts 确定性补指代实体；ingest 检索 user_memory 经 render_hints 带非权威声明注入 respond；persist 抽取并 upsert。另加 agents/v5_memory.py + registry v5；改用矩阵新规则 6b 取代节点里的事后钳位；修测试隔离（tests/conftest.py 用独立库 + fake provider + fake τ，不读 .env、不碰主库） | `caf6965` | test 607/607、lint 通过；投毒测试通过（写入可无限退款后判定逐字不变） |
| 2026-09-05 | 冲刺 | 合入 Phase 1 ③（真 RAG 接线）与 ⑤（记忆接线 + v5 agent + conftest 测试隔离）、Phase 3 审批内核、Phase 2 压缩 / 异步抽取 / 演示脚本、前端改版；真 openai provider 下 V3：26/54，硬门槛全绿，policy 61%，一致性 12/12，escalation recall 73%（τ 0.48/0.50）；8000 重启为最新 main | `e7e06c1` | test 811/811 |
| 2026-09-05 | Phase 1 | persist 节点接 P2 的 `memory.jobs.ExtractionQueue`：投递即返回，抽取与写库在后台线程（不变式 4 长期记忆异步写入、FR-704 不在热路径）；API 用异步队列并在 lifespan 回收，eval / 单测用 InlineExtractionQueue 换确定性 | `e9a8d79` | test 816/816、lint 通过；新增用例断言 persist 不等抽取（慢抽取器 1s，本轮 < 0.5s） |
| 2026-09-05 | 冲刺 | 合入 Phase 1 `e9a8d79`：persist 节点接 ExtractionQueue，长期记忆改异步写入（合并 commit `e1f1498` 的说明误写为"④ 三个工具"，以本行为准；④ 尚未交付） | `3d1fc98` | test 812/812 |
| 2026-09-05 | Phase 4 M7 | 前端拆成两个界面：`#/chat` 客户界面（客服 Tracy 人设、无后台字段、人话确认卡）与 `#/admin` 工作台（原三栏）；会话状态提到 App 层共用；登录页加入口选择 | 见 git log（frontend-chat） | tsc / lint / vitest 17 / build 通过；浏览器人工验证客户界面无后台字段、工作台看到同一会话判定 |
