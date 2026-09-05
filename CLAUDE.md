# CLAUDE.md — 项目工作规则

> 本文件是 Claude Code 在本仓库的行为约束。**每次开工第一件事：读 `HANDOFF.md`，再对照 `docs/PLAN.md` 找到自己的 milestone。**

---

## 0. 项目一句话

生产级客服 AI Agent：LLM 负责理解与提议，确定性代码负责判定与执行。
完整设计见 [`docs/PRD.md`](docs/PRD.md)。

**当前阶段：Phase 0 未开始（PRD 已定稿，待评审通过）**

---

## 1. 三条红线（违反直接拒绝合入）

1. **身份与授权永不经过 LLM**
   工具签名中不得出现 `user_id` / `tenant_id`。身份一律从 `AuthContext` 依赖注入。

2. **写操作永不由 LLM 直接触发**
   LLM 只能产出 `ActionProposal`。执行必须经过：策略引擎判定 → 用户确认或人工审批 → 幂等键。

3. **记忆永不进入授权与策略判断**
   `user_memory` / `case_state` 只能影响语气、渠道偏好、上下文提示。
   不得作为归属、退款资格、权限、金额上限的任何输入。由 CI 投毒测试拦截。

---

## 2. 沟通规则

### 2.1 回复格式（固定四段，总长不超过 15 行）

```
**做了什么**   —— 一到三句，说人话，不堆术语
**测试结果**   —— 跑了什么、过了没有、有没有指标变化
**你怎么验证** —— 具体命令 + 看什么 + 预期什么 + 不对怎么回退
**下一步**     —— 一句话
```

### 2.2 禁止

- 长篇大论、复述已经说过的内容
- 把代码大段贴进对话（除非用户要求）
- 用"完美""强大""生产级"之类的形容词自评

### 2.3 语言

文档、注释、commit message、对话一律**中文**。代码标识符、日志字段名、指标名用英文。

---

## 3. 开发节奏

```
一个 milestone
  → 我实现
  → 我自己跑测试，跑到通过
  → 我给出验证清单
  → 你人工验收
  → 你确认
  → 才进入下一个
```

**硬性约束：**

- 一次只做一个 milestone，**不许连做两个**
- 未经确认不写代码
- 加任何新依赖，先说明理由并等确认
- 大功能开工前先说计划，等一句"开始"

---

## 4. Git 规则

### 4.1 分支与 PR

- 每个 Phase 一个分支：`phase0-eval-foundation`、`phase1-skeleton` …
- 分支内小步 commit，**每完成一个可验证的小单元就 commit + push**
- Phase 完成后开 PR 合入 `main`，合入后打 tag：`v0.1-phase1`
- `main` 保持随时可运行

### 4.2 Commit message

中文，格式 `<类型>: <说明>`：

```
feat:  新功能
fix:   修复
docs:  文档
test:  测试
refactor: 重构
chore: 杂项
```

正文说明**改了什么、为什么**，末尾带 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`。

### 4.3 禁止

- 不提交 `.env`（只提交 `.env.example`）
- 不提交密钥、token、真实用户数据
- 不 force push 到 `main`

---

## 5. 测试纪律

### 5.1 每次改动必须

1. 跑相关单元测试，全部通过
2. 跑相关 eval 用例，**确认没有回归**
3. 安全类指标（授权越权、越权退款、注入抵抗、引用—执行一致性）为**硬门槛**，任一失败即视为未完成

### 5.2 绝对禁止

- **改测试来让测试通过**。测试不过说明代码有问题，不是测试有问题。
  确实需要改测试时，必须单独说明理由并等确认。
- **eval 分数下降后不报告**。任何指标下降必须主动说出来，附上原因分析。
- 跳过 eval 直接说"完成了"

### 5.3 验证清单格式

每个 milestone 结束时给用户的清单固定四行：

```
怎么跑：   <具体命令>
看什么：   <看哪个文件 / 哪个输出 / 哪个页面>
预期什么： <应该看到什么>
不对怎么办：git revert <hash>  或  git checkout <tag>
```

---

## 6. 记录文档

两个文件，职责不同，**不要混在一起**。

### 6.1 `PROGRESS.md` —— 追加式时间线

每次有实质行动就追加一行，永不删除历史：

```
| 日期 | Phase | 做了什么 | commit | 验证状态 |
```

### 6.2 `HANDOFF.md` —— 覆盖式当前快照

每次收工必须更新。新 session 靠它接手，所以要短、要准：

```
## 当前状态
当前 Phase / 当前分支 / 最新 commit

## 下一步要做什么
一到三条

## 未决问题
等用户拍板的事

## 已知坑
踩过的坑、绕过的问题、临时方案
```

### 6.3 `docs/PLAN.md` —— 跨 Phase 的 checklist

地图。每个 Phase 的 milestone 与 DoD 逐条带复选框、负责 session、完成日期与 commit。
**完成必须勾并填 commit；计划里没有的工作先加进 PLAN 再动手**；计划外事项记入末尾"偏离记录"。

### 6.4 决策记录

架构决策写 `docs/adr/`，一个决策一篇，不改旧的，只加新的（用 `Superseded by ADR-XXXX` 标记）。

---

## 7. 分层边界

| 层 | 职责 | 明确禁止 |
|---|---|---|
| FastAPI route | 参数校验、鉴权、调 orchestrator、序列化 | 写业务逻辑、直接访问 Repository |
| Agent (LangGraph) | 编排流程与状态 | 直接写数据库、绕过 Service |
| Tool | 把 Service 暴露给 LLM、校验参数 | 包含业务规则、直接写业务库 |
| Policy Engine | 纯函数策略判定 | 任何 IO、任何 LLM 调用 |
| Decision Layer | 有序规则表 → DecisionOutcome | 调用 LLM |
| Service | 业务逻辑、事务边界 | 关心 HTTP 或 LLM 概念 |
| Repository | 数据访问、强制 scope | 包含业务规则 |

**额外禁止：**

- 禁止跨 schema JOIN（SQL 中不得同时出现 `biz.` 与 `agent.` 表）
- 禁止在 `main.py` 里堆逻辑
- 禁止 LLM 写入 `CaseFacts`（只能由确定性代码从工具结果或策略判定填充）

---

## 8. 记忆四条不变式

1. 授权 / 策略判断的输入只能来自**业务库**与**策略规则**
2. `CaseFacts` 只能被确定性代码写入
3. 压缩只作用于叙述部分，`CaseFacts` 与 `pending_action` 永不参与压缩
4. 长期记忆写入是异步的、带置信度与来源的、可删除的

---

## 9. 并行工作

### 9.1 什么时候可以并行

| 阶段 | 并行策略 |
|---|---|
| Phase 0–2 | **单窗口**。接口和 schema 还在变，隔离只会把冲突推迟到合并时 |
| Phase 0 内部 | 可用 subagent 并行写**文件不相交**的产物（seed 数据 / golden dataset / policy YAML） |
| Phase 3+ | 可用 `git worktree` 多 session 并行 |

### 9.2 多 session 隔离方式

**分支不够**——同一目录的两个 session 共用一个工作区，一方切分支会把另一方的文件换掉。必须用 worktree：

```bash
git worktree add ../ca-phase3 -b phase3
git worktree add ../ca-phase4 -b phase4
```

各窗口在各自目录工作，最后各开 PR 合回 `main`。

### 9.3 并行前提

- 任务之间**文件不相交**
- 共享接口已经定稿
- 开工前在 `HANDOFF.md` 里写清谁在动哪些文件

---

## 10. 常用命令

> Phase 0 建立后填入实际命令。

```bash
make dev      # 起本地服务（含 pg + langfuse）
make test     # 跑全部单测
make eval     # 跑 golden dataset，输出报表
make migrate  # 执行数据库迁移
make seed     # 灌入 biz 种子数据
make lint     # 格式与静态检查
```

---

## 11. Definition of Done 模板

一个 milestone 只有全部满足才算完成：

- [ ] 功能实现，符合 PRD 中对应 FR 的验收标准
- [ ] 单元测试通过
- [ ] 相关 eval 用例无回归，安全类指标全绿
- [ ] `PROGRESS.md` 已追加记录
- [ ] `HANDOFF.md` 已更新
- [ ] 已 commit + push
- [ ] 已给出验证清单，用户已确认
