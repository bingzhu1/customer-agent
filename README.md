# cs-agent —— 生产级客服 AI Agent

LLM 负责理解与提议，确定性代码负责判定与执行。完整设计见 [`docs/PRD.md`](docs/PRD.md)，
架构决策见 [`docs/adr/`](docs/adr/)，工作规则见 [`CLAUDE.md`](CLAUDE.md)。

## 本地启动

```bash
cp .env.example .env         # 填入 ANTHROPIC_API_KEY
make install                 # uv sync
make dev                     # Postgres(+pgvector) + Langfuse
make test
```

需要 Docker 运行时（本机用 colima：`colima start --cpu 4 --memory 6`）。

## 进度

见 [`PROGRESS.md`](PROGRESS.md)（时间线）与 [`HANDOFF.md`](HANDOFF.md)（当前快照）。
