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
| 2026-09-05 | Phase 0 | 用户拍板：成本目标 $0.05 维持；授权安装工具。装 uv/docker CLI/docker-compose/colima；开分支 `phase0-eval-foundation`；写 pyproject(uv)、docker-compose(pg+pgvector+Langfuse v3)、.env.example、Makefile、Settings 与首个单测 | `d61b2f0` | test 3/3、lint 通过；pg 双 schema + pgvector 验证通过；Langfuse 3.225.7 健康（宿主端口 3001） |
| 2026-09-05 | Phase 0 | 共享地基：领域枚举、策略 YAML schema、golden 用例 schema、夹具契约 `docs/phase0-fixtures.md` | `9e89864` | test 12/12、lint 通过 |
| 2026-09-05 | Phase 0 | milestone 2（三路 subagent 并行）：SQLAlchemy 模型 + Alembic 初始迁移 + 可复现 seed（20 用户 / 60 单）；11 条策略 YAML；54 条 golden dataset；跨产物一致性测试；按反馈加固两个 schema（any_of、引用非空、informational 禁字段、anchor 与 domain 一致等） | `4f8bcf5` | test 95/95、lint + mypy strict 通过；migrate + seed 端到端通过 |
| 2026-09-05 | Phase 0 | 锁定被测接口 `eval/protocol.py`（AgentUnderTest / TurnResult），HANDOFF 登记三 session 并行分工 | `ba08ac8` | test 97/97 |
| 2026-09-05 | Phase 0 | milestone 3：eval runner —— 断言引擎、副作用探针（查库不信自述）、跨轮特判（幂等恰好一次 / 存在性模板一致）、§12.4 指标、markdown+JSON 报表、eval_runs 落库、LLM judge（可选）、哑 agent、`make eval` | `8cc6365` | test 117/117、lint + mypy 通过；`make eval AGENT=dummy` 1/54、硬门槛 FAIL（预期） |
