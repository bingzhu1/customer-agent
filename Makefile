# 常用命令（CLAUDE.md §10）。Phase 0 先落 dev / db / test / lint，其余随 Phase 补齐。
.PHONY: dev dev-db down logs install test lint fmt eval migrate seed psql

install:          ## 安装 Python 依赖（uv 管理虚拟环境）
	uv sync

dev:              ## 起全部本地服务（pg + langfuse），Langfuse 首次启动约 1–2 分钟
	docker compose up -d
	@echo "Postgres: localhost:5432   Langfuse: http://localhost:$${LANGFUSE_PORT:-3000}"

dev-db:           ## 只起 Postgres（跑单测 / eval 够用）
	docker compose up -d postgres

down:             ## 停止服务（保留数据卷）
	docker compose down

logs:             ## 跟踪服务日志
	docker compose logs -f --tail=100

psql:             ## 进入数据库
	docker compose exec postgres psql -U cs_agent -d cs_agent

test:             ## 跑全部单测
	uv run pytest -q

lint:             ## 格式与静态检查
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt:              ## 自动格式化
	uv run ruff format .
	uv run ruff check --fix .

migrate:          ## 执行数据库迁移（Alembic，版本表在 agent schema）
	uv run alembic upgrade head

seed:             ## 灌入 biz 种子数据（幂等，可重复执行）
	uv run python -m cs_agent.seed.biz_seed

AGENT ?= dummy
EVAL_ARGS ?=
eval:             ## 跑 golden dataset，输出报表。例：make eval AGENT=v0 EVAL_ARGS="--judge"
	uv run python -m cs_agent.eval --agent $(AGENT) $(EVAL_ARGS)
