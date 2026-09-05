# HANDOFF — 当前状态快照

> 覆盖式文档，每次收工必须更新。新 session 开工先读这里。
> 历史记录见 [`PROGRESS.md`](PROGRESS.md)。

**最后更新：2026-09-05**

---

## 当前状态

- **Phase**：Phase 0 进行中 —— milestone 1（环境与项目骨架）已完成，等用户验收
- **分支**：`phase0-eval-foundation`（从 `main` 切出，尚未开 PR）
- **最新 commit**：见 `git log -1`
- **仓库**：https://github.com/bingzhu1/customer-agent （public）
- **模型**：本 session 为 Fable 5.1，上一 session 的模型切换问题已不存在

## 本 milestone 产物

- 工具链：`uv`、`docker` CLI、`docker-compose`（已注册为 `docker compose` 插件）、`colima`（Docker 运行时，`colima start --cpu 4 --memory 6 --disk 40`）
- `pyproject.toml`（uv 管理，Python 3.12，依赖：anthropic / pydantic / sqlalchemy / psycopg / alembic / structlog / langfuse / pyyaml；dev：pytest / ruff / mypy）
- `docker-compose.yml`：`pgvector/pgvector:pg16` + Langfuse v3 全套（web / worker / clickhouse / redis / minio）
- `docker/postgres/init/01-init.sql`：建 `biz` / `agent` schema、`vector` 扩展、独立 `langfuse` 库
- `.env.example`、`Makefile`（dev / dev-db / down / test / lint / fmt，migrate / seed / eval 为 TODO 占位）
- `src/cs_agent/settings.py`（pydantic-settings 集中配置）+ `tests/test_settings.py`
- 本机 `.env` 已从模板生成，**`ANTHROPIC_API_KEY` 仍是占位符，需用户填入**；`LANGFUSE_PORT=3001`

## 下一步要做什么

1. 用户验收 milestone 1（验证清单见最后一次回复）
2. milestone 2：Phase 0 首批数据产物，文件不相交，可用 subagent 并行：
   - `biz` seed 数据（约 20 用户 / 60 订单，含超期 / 食品 / 定制 / 高额边界样本）—— 需先定 Alembic 初始迁移（表结构见 PRD §7.2）
   - `policies/*.yaml`（退款 / 物流 / 保修 / 会员 / 投诉），格式见 PRD §9.2
   - `data/golden/*.yaml` 34–54 条，格式见 PRD §12.3，分类与条数见 §12.2
3. milestone 3：eval runner（直接调用 agent 接口，不走 HTTP）+ V0 naive baseline 实测 + markdown 报表进版本库
4. Phase 0 的 DoD 见 PRD §15

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
- `negativexq/agentic-customer-service-platform` 尚未验证是否存在，PRD 相关设计为独立推导。
- Phase 0–2 **不要多 session 并行**：接口与 schema 还在变。Phase 3+ 再用 `git worktree`。
- checkpoint 恢复会**重放节点**。本版靠数据库唯一约束防重复副作用；接入真实外部服务后必须上 transactional outbox（PRD §17）。
