# HANDOFF — 当前状态快照

> 覆盖式文档，每次收工必须更新。新 session 开工先读这里。
> 历史记录见 [`PROGRESS.md`](PROGRESS.md)。

**最后更新：2026-09-05**

---

## 当前状态

- **Phase**：Phase 0 未开始（PRD 与工作规则已成文，等用户评审通过）
- **分支**：`main`
- **最新 commit**：见 `git log -1`
- **仓库**：https://github.com/bingzhu1/customer-agent （private）
- **代码**：尚未写任何代码。目前只有文档。

---

## 已完成

- `docs/PRD.md` v1.0 —— 完整产品与技术需求文档（18 章）
- `CLAUDE.md` —— 工作规则、三条红线、分层边界、Git/测试/并行规范
- `docs/adr/0001` ~ `0009` —— 九篇架构决策记录
- `.gitignore`

---

## 下一步要做什么

1. 等用户评审 `docs/PRD.md` 和 `CLAUDE.md`，按反馈修订
2. 评审通过后开分支 `phase0-eval-foundation`，进入 Phase 0
3. Phase 0 首批产物（文件不相交，可用 subagent 并行）：
   - `biz` seed 数据（约 20 用户 / 60 订单，含超期、食品、定制、高额边界样本）
   - `policies/*.yaml`（退款 / 物流 / 保修 / 会员 / 投诉）
   - golden dataset 34–54 条

---

## 未决问题

| # | 问题 | 等谁 |
|---|---|---|
| 1 | PRD §13.1 的性能目标数字（p95 < 6s、单会话 < $0.05）是否认可 | 用户 |
| 2 | LLM provider 选哪家、哪个模型档位（影响成本估算与 fallback 设计） | 用户 |
| 3 | 仓库是否要改为 public（面试展示用） | 用户 |
| 4 | ADR-0007 的 τ_low / τ_high 实测值 | Phase 2 标定后回填 |

---

## 已知坑

- `negativexq/agentic-customer-service-platform` 尚未验证是否存在，
  PRD 中相关设计均为独立推导，不作为权威引用。
- Phase 0–2 **不要多 session 并行**：接口与 schema 还在变，
  隔离只会把冲突推迟到合并时。Phase 3+ 再用 `git worktree`。
- checkpoint 恢复会**重放节点**。本版靠数据库唯一约束防重复副作用；
  一旦接入真实外部服务，必须上 transactional outbox（PRD §17 第 1 条）。
