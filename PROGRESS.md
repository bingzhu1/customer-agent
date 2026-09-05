# PROGRESS — 行动时间线

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
| 2026-09-05 | Phase 0 | 用户拍板：成本目标 $0.05 维持；授权安装工具。装 uv/docker CLI/docker-compose/colima；开分支 `phase0-eval-foundation`；写 pyproject(uv)、docker-compose(pg+pgvector+Langfuse v3)、.env.example、Makefile、Settings 与首个单测 | （待填） | test 3/3、lint 通过；pg 双 schema + pgvector 验证通过；Langfuse 3.225.7 健康（宿主端口 3001） |
