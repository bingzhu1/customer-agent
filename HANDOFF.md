# HANDOFF — 当前状态快照

> 覆盖式文档，每次收工必须更新。新 session 开工先读这里。
> 历史记录见 [`PROGRESS.md`](PROGRESS.md)。

**最后更新：2026-09-05**

---

## 当前状态

- **Phase**：Phase 0 与 Phase 3（策略引擎 + 决策矩阵）已在 `main`；本分支做 Phase 1，**3 小时冲刺进行中**，分工见 `docs/PLAN.md` 顶部
- **分支**：`phase1-skeleton`（worktree `~/Desktop/ca-phase1`），已 rebase 到 `origin/main`
- **最新 commit**：见 `git log -1`
- **仓库**：https://github.com/bingzhu1/customer-agent （public）
- **数据库**：本 worktree 用**独立库 `cs_agent_p1`**（`.env` 中 DATABASE_URL 已指向它）。**不要改回 `cs_agent`。**
  库是手工建的（`CREATE DATABASE cs_agent_p1` + `CREATE EXTENSION vector` + biz/agent 两个 schema）；
  迁移往返测试用的一次性库由 `tests/test_migrations.py` 按 worktree 路径哈希自建自删。

## 本分支已交付（Phase 1 milestone 1–2）

- `alembic/versions/0002_agent_platform.py` + `db/models/agent.py`：PRD §7.3 其余 10 张 agent 表
  （UNIQUE(idempotency_key)、policy_chunks 版本唯一键；向量列 Text 占位，Phase 2 换 pgvector）
- `auth/context.py`（`AuthContext(user_id, roles)`）+ `auth/jwt.py`（HS256，显式 algorithms 防 alg 混淆）
- `repositories/biz.py`：`get_order / get_shipping / get_ticket` 强制 `WHERE user_id = ctx.user_id`，
  他人与不存在**一律返回 None**（FR-804）
- `api/`：`main.py` 只做装配；middleware 顺序 request_id → 指标 → 认证；`errors.py` 是 §8.4 统一信封；
  `routes/ops.py`（/health /ready /metrics）、`routes/v1.py`（目前只有 `GET /v1/whoami`）
- `observability/`：structlog + Prometheus 指标
- `make serve` 起服务、`make token USER=101` 签调试 token

## 下一步要做什么

1. 冲刺第一段：LangGraph 最小图 ingest→understand→act→decide→respond（checkpointer 先用 MemorySaver）
   + 4 个只读工具（search_policy 暂为关键词匹配，非真 RAG）
2. 冲刺第二段：`agents/v1_tools.py` 实现 `AgentUnderTest`，registry 加 `v1`，跑 `make eval AGENT=v1`，
   目标 authorization violation = 0
3. 冲刺第三段：decide 调 `decision.matrix.decide`，policy_gate 用 Repository 实时查事实构造 `PolicyFacts`
   后调 `policy.engine.evaluate`；`agents/v3_policy.py`，registry 加 `v3`，跑 `make eval AGENT=v3`
4. 冲刺不做：SSE、Langfuse、限流、prompt caching、写操作执行；`POST /v1/threads` 等 REST 接口也押后

## 并行分工（2026-09-05 起，Phase 0 例外放开）

共享接口 `src/cs_agent/eval/protocol.py` 已锁定；改它必须先在此处声明并通知所有 session。
每个 worktree 用**独立数据库名**（seed 会清空 biz 表，alembic 版本表不能共用）。各分支只 push 不合并，合并顺序由 master 定。

| session | 分支 / 目录 | 只能动的文件 | 数据库 |
|---|---|---|---|
| master（本 session） | `phase0-eval-foundation` / 主目录 | `src/cs_agent/eval/**`（protocol 除外，改动需声明）、`tests/test_eval_*`、Makefile 的 eval target、`eval_reports/`、PROGRESS / HANDOFF | `cs_agent` |
| session 2：V0 baseline | `phase0-v0-baseline` / `../ca-v0` | **已交付并合入**，session 可关闭 | — |
| session 4：Phase 3 → Phase 4 内核 → 写路径闭环 | `phase3-policy-engine` / `../ca-phase3` | `policy/facts.py`、`policy/engine.py`、`decision/**`、`actions/**`、`services/refund.py`、`services/human_review.py`、对应 tests；**临时借用 P1 的 4 个文件做 confirm API**（`services/chat.py`、`api/schemas.py`、`api/routes/v1.py`、`api/errors.py`，P1 承诺此期间不动） | `cs_agent_p3` |
| session 5：Phase 2 → Phase 5 记忆 | `phase2-rag` / `../ca-phase2` | `rag/**`（除 `rag/provider.py`，该文件为 P1 新建、归 P1）、`memory/**`、`scripts/calibrate_tau.py`、`docs/adr/0007` | `cs_agent_p2` |
| session 6：前端 | `frontend-chat` / `../ca-frontend` | `frontend/**` | 无 |
| session 3：Phase 1 | `phase1-skeleton` / `../ca-phase1` | Phase 1 全部范围（用户已在该 session 内验收 2 个 milestone：0002 迁移 + AuthContext + Repository；FastAPI / JWT / 可观测性，新依赖 fastapi / uvicorn / pyjwt / prometheus-client / httpx 已获用户同意）。共享文件改动：settings.py 加 jwt_*；test_migrations 合并时以 master 的一次性库版为准 | `cs_agent_p1` |
| FE（前端） | `frontend-chat` / `../ca-frontend` | `frontend/**`、`.gitignore` 的 frontend 两行、本表这一行。**2026-09-05 晚起 master session 在此分支做视觉改版（方案 A 三栏）**：动 `styles.css`、`index.html`、`pages/**`、`timeline/*Item.tsx`、新增 `components/**` 与 `timeline/workspace.ts`；不动 `api/**` | 无数据库（对 mock，后接真实 API） |

合并顺序（用户 2026-09-05 拍板）：**等 V0 交付后**一并合——V0 → phase0-eval-foundation → main（tag `v0.1-phase0`）→ phase1-skeleton rebase 到 main。
`uv.lock` 不手工合，合并后重跑 `uv sync`。

### 2026-09-05 三方对齐结论

- V0 session 此前因缺 `protocol.py` 停工，已 fetch 到 ba08ac8 并开工；计划 `agents/v0_naive.py` 的 `V0NaiveAgent`，client 注入式 mock。
- `/v1/whoami` 保留并已补进 PRD §8.1（v1.1）。
- 接口反馈处理：并发用线程已写进 protocol 文档；Usage 按模型拆分推到 Phase 6；`retrieved` 字段推到 Phase 2。

## 未决问题

| # | 问题 | 等谁 |
|---|---|---|
| 1 | 向量化 provider：OpenAI `text-embedding-3-small` vs Voyage AI | 用户，Phase 2 前 |
| 3 | ADR-0007 的 τ_low / τ_high 实测值 | Phase 2 标定后回填 |

### 已定（2026-09-05）

- **前端**：正式页面但 demo 级，Vite + React + TS 放 `web/`，无组件库；对话页 = Phase 4 M7，审批页 = Phase 6 M6（PLAN 已加）。设计借鉴 embedease-ai 的 chat-sdk（纯 reducer），不复制代码

- 单会话成本目标 **$0.05 维持**；Phase 6 前只记录不考核（原未决问题 2）
- 主模型 Claude Sonnet 5（`claude-sonnet-5`），降级 Claude Haiku 4.5（`claude-haiku-4-5`）
- Docker 运行时用 colima 而非 Docker Desktop（无需 sudo、无 GUI）
- `GET /v1/whoami` **保留**，master session 已补进 PRD §8.1（v1.1，在 phase0 分支上）
- **合并顺序**：V0 交付 → `phase0-eval-foundation` → `main`（tag `v0.1-phase0`）→ 本分支再 rebase 到 main。
  合并时 `tests/test_migrations.py` 以 master 的一次性库（`cs_agent_test`）版为准；
  `uv.lock` 不手工合，合并后重跑 `uv sync` 重新生成
- `AgentUnderTest` 接口已在 phase0 分支定稿（`ba08ac8`）：**同步**接口；
  runner 对并发 confirm 用线程池，async 实现要自持事件循环并保证线程安全

## 已知坑

- **本机 3000 端口被用户另一个项目占用**（`~/Desktop/bingzhu's file/spam` 的 tsx watch 服务），
  因此 Langfuse 宿主端口通过 `LANGFUSE_PORT` 参数化，本机用 3001。不要杀那个进程。
- Homebrew 的 `docker-compose` 不会自动注册为 `docker compose` 子命令，需
  `ln -sfn /opt/homebrew/opt/docker-compose/bin/docker-compose ~/.docker/cli-plugins/docker-compose`。
- colima 默认 2 核 2G 跑不动 ClickHouse，必须显式给 `--cpu 4 --memory 6`。重启机器后需 `colima start`。
- Langfuse v3 首次启动要跑 Postgres 与 ClickHouse 迁移，约 1–2 分钟内 API 不可用是正常的。
- golden 中多个可接受结果的用例用 `decision_any_of` / `reason_code_any_of`，runner 必须支持；
  SEC-010（两轮回复模板一致）与 IDEM-002（并发中恰好一次 replay）需要 runner 跨轮特判，notes 里有说明。
- RAG-007/008 的低置信引用断言用 `citations_must_not_be_empty`，Phase 2 标定 τ 后再复核具体 id。
- seed 每次全量清空 biz 7 表再重灌；biz 完全由 seed 拥有，不要手工往里插数据。
- **跑一次 `make test` 会把主库 `agent.policy_chunks` 灌成 fake 向量**（test_agent_v3 / test_api_threads 的 fixture 直接写 DATABASE_URL 主库），demo 服务的真 RAG 会立刻失效（分数掉到 0.05）。P1 修隔离前，测试后必须 `uv run python -m cs_agent.rag.ingest` 重灌 openai 向量。
- `.env` 里 `EMBEDDING_PROVIDER=openai`、`RAG_TAU_LOW=0.48`、`RAG_TAU_HIGH=0.50`（0.60 会切掉一半正样本；openai 下 82913 退款问句 max_score≈0.57）；fake provider 的 τ 是代码常量 0.28/0.40，不走 settings。
- **Anthropic 余额耗尽表现为全局 DEGRADE / DEPENDENCY_UNAVAILABLE**（400 "credit balance is too low"），eval 报表会看起来像"全挂"。2026-09-05 晚 V5 首跑因此无效已删。跑 eval 前先 `uv run python -c` 打一次 Haiku 探活。
- **两处静默降级**（P2 提醒）：`calibrate_tau.py --rewrite` 在 Haiku 失败时回退到确定性 query 且不报错，此时 τ 不可采用（脚本将加 source 列防呆）；`memory_demo.py --real` 抽取失败会被队列吞掉，演示用默认模式（确定性假抽取器，不触网）。
- **同一版本两次 eval 结果可能不同**（V1：19/54 vs 17/54）。安全类指标不接受随机：任何依赖 LLM 结构化输出才能触发的拒绝（如冒充身份）都必须有不依赖 LLM 的确定性兜底。非 ANSWER 终态的回复必须逐字用 decision/templates，LLM 不得改写。
- **分支同步用 `git merge origin/main`，不要 rebase**：rebase 重写历史会让已合并的提交再次出现冲突（P1 踩过三次）。
- Phase 1 的 API 测试从 `.env` 读 `JWT_SECRET`，本地 `.env` 缺它会报 `HMAC key must not be empty`；已从 `.env.example` 补齐。测试不该依赖 `.env`，记入 PLAN 待补。
- 迁移往返测试用一次性库 `cs_agent_test_<仓库路径哈希>`，每次先 DROP 再 CREATE；不同 worktree 迁移 head 不同，**绝不能共用同名测试库**（踩过：Phase 1 把共用库升到 0002，main 找不到该版本）。
- runner 的副作用判定只看探针快照（biz.refunds / tickets 计数），被测方自述的 reason_code 不作为写库证据。
- 并发 confirm 的"代表结果"取 IDEMPOTENT_REPLAY 那一次；cost 估算按本轮用到的最贵模型计（Usage 不按模型拆分）。
- `registry` 通过"模块内唯一的 AgentUnderTest 子类"发现 V0；若 session 2 放了多个类，需在模块里加 `AGENT = 类名`。
- `negativexq/agentic-customer-service-platform` 尚未验证是否存在，PRD 相关设计为独立推导。
- Phase 0–2 **不要多 session 并行**：接口与 schema 还在变。Phase 3+ 再用 `git worktree`。
- checkpoint 恢复会**重放节点**。本版靠数据库唯一约束防重复副作用；接入真实外部服务后必须上 transactional outbox（PRD §17）。
- Phase 1 的 agent 表**不向 biz 表建外键**（`threads.user_id` 只是普通整数列）：
  跨 schema 耦合会把两套系统绑死，归属校验由 Repository 层负责。
- `policy_chunks` 的 `metadata` 列在模型里叫 `chunk_metadata`：`metadata` 是 DeclarativeBase 保留名。
- `memory_embeddings.embedding` / `policy_chunks.embedding` 现在是 **Text 占位**，
  Phase 2 换 pgvector `vector(1536)` 并建 HNSW 索引，届时需要一次 ALTER 迁移。
- Phase 0 的 milestone 3（eval runner）与 milestone 4（V0 baseline）**尚未完成**，
  Phase 0 未开 PR；Phase 1 先行，最后一并合入。
- `GET /v1/whoami` 是本分支加的**认证自检接口**；用户已拍板保留，PRD §8.1 v1.1 已补上。
- 中间件里**不能 raise `ApiError`**：异常处理器挂在更内层，中间件抛出的异常会直接变 500，
  必须 `return error_response(...)`。
- `BaseHTTPMiddleware` 的 `call_next` 在独立 task 里跑，内层绑的 contextvars 传不回外层，
  所以访问日志的 `request_id` / `user_id` 是显式传参而不是靠 contextvars。
- `structlog.testing.capture_logs()` 会丢掉 `merge_contextvars`，测日志字段要断言显式传的参数。
- Prometheus 指标必须定义在模块顶层（默认 registry 只能注册一次），
  否则 `create_app()` 被测试多次调用会抛重复注册。
- rebase 后**两个"回填 commit hash"的 commit 被 skip 了**（hash 已变，重填没意义），
  PROGRESS 里 Phase 1 的 hash 是 rebase 后的新值：milestone 1 = `6eb8984`、milestone 2 = `4942f73`。
- 迁移往返测试现在跑在一次性库 `cs_agent_test` 上（phase0 的做法），不再动开发库 `cs_agent_p1`。
