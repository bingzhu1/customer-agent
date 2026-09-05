# HANDOFF — 当前状态快照

> 覆盖式文档，每次收工必须更新。新 session 开工先读这里。
> 历史记录见 [`PROGRESS.md`](PROGRESS.md)。

**最后更新：2026-09-05**

---

## 当前状态

- **Phase**：Phase 0 进行中 —— milestone 1（环境骨架）已验收；milestone 2（数据产物）已完成，等用户验收
- **分支**：`phase0-eval-foundation`（从 `main` 切出，尚未开 PR）
- **最新 commit**：见 `git log -1`
- **仓库**：https://github.com/bingzhu1/customer-agent （public）
- **模型**：本 session 为 Fable 5.1，上一 session 的模型切换问题已不存在

## 本 milestone（2）产物

- `docs/phase0-fixtures.md`：seed / policy / golden 三路共用的 id 与事实契约（**改 id 必须三路同步**），§8 是 effect → 决策映射
- `src/cs_agent/domain/enums.py`、`policy/schema.py`、`eval/schema.py`：受限枚举与两份 pydantic schema
- `src/cs_agent/db/`（biz 7 表 + agent.eval_runs/eval_results）、`alembic/`（迁移 0001）、`src/cs_agent/seed/`（`EVAL_NOW = 2026-09-01T00:00:00Z` 在 `seed/reference.py`，策略引擎与 runner 都要用它当"今天"）
- `policies/*.yaml` 11 条规则；`data/golden/*.yaml` 54 条用例（security / memory 全部逐条 review，rag 4 条逐条）
- 测试 95 个：schema 形状、策略一致性、golden 一致性、seed 对契约参数化、迁移往返、跨产物交叉核对
- `make migrate` / `make seed` 已可用；`make eval` 仍是 TODO

## 下一步要做什么

1. 用户验收 milestone 2（验证清单见最后一次回复）
2. milestone 3：eval runner —— `make eval` 一条命令跑完 54 条，输出 markdown 报表到 `eval_reports/` 并写 `agent.eval_runs/eval_results`；
   runner 直接调用一个 `AgentUnderTest` 接口（不走 HTTP），断言逻辑覆盖 `Expect` 全部字段（含 any_of、citations_must_not_be_empty、no_certainty_wording）
3. milestone 4：V0 naive baseline（裸 LLM，无工具无检索）接入 runner 实测，报表进版本库，Phase 0 收官开 PR
4. Phase 0 的 DoD 见 PRD §15

## 并行分工（2026-09-05 起，Phase 0 例外放开）

共享接口 `src/cs_agent/eval/protocol.py` 已锁定；改它必须先在此处声明并通知所有 session。
每个 worktree 用**独立数据库名**（seed 会清空 biz 表，alembic 版本表不能共用）。各分支只 push 不合并，合并顺序由 master 定。

| session | 分支 / 目录 | 只能动的文件 | 数据库 |
|---|---|---|---|
| master（本 session） | `phase0-eval-foundation` / 主目录 | `src/cs_agent/eval/**`（protocol 除外，改动需声明）、`tests/test_eval_*`、Makefile 的 eval target、`eval_reports/`、PROGRESS / HANDOFF | `cs_agent` |
| session 2：V0 baseline | `phase0-v0-baseline` / `../ca-v0` | `src/cs_agent/agents/__init__.py`、`agents/v0_naive.py`、`tests/test_v0_naive.py` | `cs_agent`（只读，不跑 seed） |
| session 3：Phase 1 预备 | `phase1-skeleton` / `../ca-phase1` | `alembic/versions/0002_*.py`、`db/models/agent.py`（只追加）、`src/cs_agent/auth/**`、`src/cs_agent/repositories/**`、对应 tests | `cs_agent_p1` |

合并顺序：V0 → phase0-eval-foundation → main（打 tag v0.1-phase0）→ phase1-skeleton rebase 到 main。

## 未决问题

| # | 问题 | 等谁 |
|---|---|---|
| 1 | 向量化 provider：OpenAI `text-embedding-3-small` vs Voyage AI | 用户，Phase 2 前 |
| 3 | ADR-0007 的 τ_low / τ_high 实测值 | Phase 2 标定后回填 |

### 已定（2026-09-05）

- 单会话成本目标 **$0.05 维持**；Phase 6 前只记录不考核（原未决问题 2）
- 主模型 Claude Sonnet 5（`claude-sonnet-5`），降级 Claude Haiku 4.5（`claude-haiku-4-5`）
- Docker 运行时用 colima 而非 Docker Desktop（无需 sudo、无 GUI）

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
- `negativexq/agentic-customer-service-platform` 尚未验证是否存在，PRD 相关设计为独立推导。
- Phase 0–2 **不要多 session 并行**：接口与 schema 还在变。Phase 3+ 再用 `git worktree`。
- checkpoint 恢复会**重放节点**。本版靠数据库唯一约束防重复副作用；接入真实外部服务后必须上 transactional outbox（PRD §17）。
