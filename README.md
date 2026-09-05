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


## V0 → V3 演进（同一套 54 条 golden，2026-09-05 实测）

| 版本 | 内容 | success | 安全硬门槛 | authz 越权 | 注入抵抗 | policy correctness | 引用—执行一致 | security 类 | policy 类 | $/session |
|---|---|---|---|---|---|---|---|---|---|---|
| **V0** naive | 裸 LLM，无工具无检索 | 1.9%（1/54） | ❌ | 7 | 50% | 33% | n/a | 1/10 | 0/10 | $0.011 |
| **V1** +tools | 4 只读工具 + Repository 强制 scope + 决策矩阵 | 35.2%（19/54） | ✅ | 0 | 100% | 44% | n/a | 10/10 | 2/10 | $0.011 |
| **V3** +policy | 确定性策略引擎 + 引用—执行一致性 + 模板化拒绝 | 46.3%（25/54） | ✅ | 0 | 100% | 56% | 100%（12/12） | 10/10 | 6/10 | $0.011 |

读法：V0 → V1 解决的是**安全**（越权 7 → 0，注入 50% → 100%），V1 → V3 解决的是**判得对**（policy 类 2 → 6，引用与执行 100% 一致）。
memory 类三版都是 0/8，escalation 与 rag 类还低——这正是 Phase 5（记忆）和 Phase 2 接线（真 RAG）要做的事，见 `docs/PLAN.md`。
每版报表在 `eval_reports/latest_<agent>.md`；裸 LLM 的错误分析在 `docs/eval/v0-baseline.md`。

## 进度

见 [`PROGRESS.md`](PROGRESS.md)（时间线）与 [`HANDOFF.md`](HANDOFF.md)（当前快照）。
