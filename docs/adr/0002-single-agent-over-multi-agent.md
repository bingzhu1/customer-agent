# ADR-0002：第一版采用单 Agent 而非 multi-agent

- 状态：已接受
- 日期：2026-09-05
- 相关：PRD §6.4

## 背景

客服场景很容易被拆成 Router + Order Agent + Refund Agent + Technical Support Agent +
Complaint Agent。这个拆法在图上很好看，但需要论证它解决了什么问题。

## 决策

**第一版使用单个强类型 LangGraph workflow + 不超过 7 个工具。**

节点是**处理阶段**（ingest → understand → retrieve → act → policy_gate → decide →
execute/interrupt → respond → compress → persist），不是业务角色。

## 理由

拆分为多 Agent 的成本是真实且立刻发生的：

1. Router 误分类会把错误提前引入，且难以归因
2. 跨 Agent 的状态同步需要额外设计（谁持有 CaseFacts？谁负责 checkpoint？）
3. trace 可读性下降，排障成本上升

而收益在当前条件下为零：单业务域、单权限边界、工具数量个位数。

**拆 sub-agent 的正当理由是工具选择准确率下降或权限边界不同，
不是"业务上属于不同部门"。**

## 备选方案

| 方案 | 否决理由 |
|---|---|
| Router + 4 个专职 Agent | 无法说出它解决了哪个已观测到的问题 |
| 一个 ReAct agent 自由循环 | 无法在固定位置插入确定性策略门；无法保证 policy_gate 一定被执行 |

## 后果

**正面**：流程固定，`policy_gate` 与 `decide` **必然**被执行，无法被模型绕过；
trace 线性可读；评估断言简单。

**负面**：工具增多后单个 prompt 会变长；需要靠评估监控工具选择准确率。

## 复审条件（四条中任意一条成立才重新讨论）

1. 评估显示工具数量增长后选择准确率跌破阈值（如 >15 个工具时 <90%）
2. 出现权限边界本质不同的业务域
3. 某个域需要不同模型或超长上下文，导致成本失控
4. **评估数据**证明单 Agent 已是瓶颈（不接受直觉判断）
