# HANDOFF — 当前状态快照

> 覆盖式文档，每次收工必须更新。新 session 开工先读这里。
> 历史记录见 [`PROGRESS.md`](PROGRESS.md)。

**最后更新：2026-09-05**

---

## 当前状态

- **Phase**：Phase 1 进行中 —— milestone 1（agent 平台表 + 身份 scope）已验收；milestone 2（FastAPI 骨架 + JWT）已完成，等用户验收
- **分支**：`phase1-skeleton`（worktree 目录 `~/Desktop/ca-phase1`，与主 checkout 隔离）
- **最新 commit**：见 `git log -1`
- **仓库**：https://github.com/bingzhu1/customer-agent （public）
- **数据库**：本 worktree 用**独立库 `cs_agent_p1`**（`.env` 中 DATABASE_URL 已指向它），
  与主 checkout 的 `cs_agent` 互不干扰。**不要改回 `cs_agent`。**
  库是手工建的（`CREATE DATABASE cs_agent_p1` + `CREATE EXTENSION vector` + 两个 schema），
  docker init 脚本只在空数据卷首次启动时跑，不会自动建这个库。

## Phase 1 milestone 2 产物

- `src/cs_agent/api/`：`main.py`（只做装配）、`middleware.py`（request_id → 指标 → 认证，
  由外到内）、`errors.py`（§8.4 统一信封，404 不区分"不存在"与"不属于你"）、
  `deps.py`（`AuthDep` / `SessionDep` / `BizRepoDep` / `require_role`）、
  `routes/ops.py`（health / ready / metrics）、`routes/v1.py`（目前只有 `GET /v1/whoami` 认证自检）
- `src/cs_agent/auth/jwt.py`：HS256 签发与校验，显式 `algorithms=["HS256"]` 防 alg 混淆；
  校验失败一律 401 `UNAUTHENTICATED`，不区分签名错/过期/缺字段
- `src/cs_agent/observability/`：structlog（contextvars 绑 request_id/user_id）与 Prometheus 指标
- 新依赖：fastapi、uvicorn[standard]、pyjwt、prometheus-client、httpx(dev)
- `make serve` 起服务、`make token USER=101` 签调试 token
- 测试 143 个（新增 API 相关 30 个）

## 树里已有的 Phase 0 产物：eval runner

- `src/cs_agent/eval/`：`protocol.py`（被测接口）· `assertions.py`（Expect → Check）· `side_effects.py`（查库探针）·
  `runner.py`（多轮驱动 + 跨轮特判）· `metrics.py`（PRD §12.4）· `report.py`（markdown + JSON）·
  `store.py`（eval_runs / eval_results）· `judge.py`（Haiku 评判语气 / groundedness，`--judge` 开启）·
  `dummy.py`（哑 agent）· `registry.py`（`--agent` 名 → 实例，v0 惰性导入 `cs_agent.agents.v0_naive`）· `__main__.py`
- `make eval AGENT=dummy|v0 EVAL_ARGS="--judge --filter SEC --no-db --strict"`
- 报表：`eval_reports/<时间戳>_<agent>.md` + `latest_<agent>.md`；JSON 同名（.gitignore 忽略）
- 基线：哑 agent 1/54，硬门槛 FAIL —— 用作 runner 的反向校验

## Phase 1 milestone 1 产物

- `alembic/versions/0002_agent_platform.py` + `src/cs_agent/db/models/agent.py`：
  PRD §7.3 其余 10 张表（threads / messages / case_state / agent_actions / human_reviews /
  audit_log / user_memory / memory_embeddings / policy_chunks / rate_limit_counters）
- `src/cs_agent/auth/context.py`：`AuthContext(user_id, roles)`，frozen dataclass，`Role` 三值
- `src/cs_agent/repositories/biz.py`：`BizRepository.get_order / get_shipping / get_ticket`，
  全部强制 `WHERE user_id = ctx.user_id`，他人与不存在**一律返回 None**（FR-804）
- `tests/test_authz.py` 15 条 + `tests/test_migrations.py` 扩充；共 113 个测试

## 下一步要做什么

1. 用户验收 Phase 1 milestone 2
2. milestone 3：`POST /v1/threads`、`GET /v1/threads/{id}`（FR-101/104，他人会话 404）
3. milestone 4：LangGraph 最小图（ingest→understand→act→respond）+ Postgres checkpointer + 4 个只读工具
4. 中间件还缺限流（FR-806，表已建）与超时；Phase 1 收官前补
5. Phase 0 遗留：eval runner（`make eval`）与 V0 baseline 尚未做，见下方"已知坑"

## 并行分工（2026-09-05 起，Phase 0 例外放开）

共享接口 `src/cs_agent/eval/protocol.py` 已锁定；改它必须先在此处声明并通知所有 session。
每个 worktree 用**独立数据库名**（seed 会清空 biz 表，alembic 版本表不能共用）。各分支只 push 不合并，合并顺序由 master 定。

| session | 分支 / 目录 | 只能动的文件 | 数据库 |
|---|---|---|---|
| master（本 session） | `phase0-eval-foundation` / 主目录 | `src/cs_agent/eval/**`（protocol 除外，改动需声明）、`tests/test_eval_*`、Makefile 的 eval target、`eval_reports/`、PROGRESS / HANDOFF | `cs_agent` |
| session 2：V0 baseline | `phase0-v0-baseline` / `../ca-v0` | `src/cs_agent/agents/__init__.py`、`agents/v0_naive.py`、`tests/test_v0_naive.py` | `cs_agent`（只读，不跑 seed） |
| session 3：Phase 1 | `phase1-skeleton` / `../ca-phase1` | Phase 1 全部范围（用户已在该 session 内验收 2 个 milestone：0002 迁移 + AuthContext + Repository；FastAPI / JWT / 可观测性，新依赖 fastapi / uvicorn / pyjwt / prometheus-client / httpx 已获用户同意）。共享文件改动：settings.py 加 jwt_*；test_migrations 合并时以 master 的一次性库版为准 | `cs_agent_p1` |

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
- 迁移往返测试用一次性库 `cs_agent_test`（自动创建），**不要**在开发库上跑 downgrade，否则 eval_runs 历史丢失。
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
