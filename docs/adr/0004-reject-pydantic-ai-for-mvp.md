# ADR-0004：MVP 不采用 PydanticAI

- 状态：已接受
- 日期：2026-09-05
- 相关：PRD §18.3

## 背景

候选技术里提到过 "PyAgent"，最可能对应的是 **PydanticAI**。
需要回答一个问题：它解决了什么 LangGraph 没解决的问题？

## 决策

**MVP 不引入 PydanticAI。** 保留在技术选型对比中作为评估过的候选。

## 理由

PydanticAI 的核心价值有三点，我们已有等价能力：

| PydanticAI 提供 | 我们的等价方案 |
|---|---|
| 强类型 agent 输入输出 | Pydantic v2 + LLM structured output |
| 依赖注入 | FastAPI 的 DI（同时也是 `AuthContext` 注入的载体） |
| 模型无关抽象 | 直接用 provider SDK；需要多 provider 时再引入适配层 |

而我们最依赖的能力——**持久化 checkpoint + 中断 + 从原处恢复**——
PydanticAI 不提供等价物。这恰恰是本项目的命脉（ADR-0003）。

同时引入两个 agent 框架会造成职责重叠、概念冲突、trace 分裂。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 用 PydanticAI 替代 LangGraph | 失去 checkpoint / interrupt / resume，项目核心卖点没了 |
| LangGraph + PydanticAI 混用 | 两套 agent 抽象重叠，收益不明确，复杂度确定增加 |

## 后果

**正面**：依赖更少；类型安全由 Pydantic 直接提供，不需要中间层。

**负面**：工具定义与结构化输出要自己接线（工作量很小）。

## 复审条件

若 PydanticAI 未来提供成熟的持久化中断/恢复，且生态优于 LangGraph，可重新评估。
