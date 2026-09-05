"""被测 Agent 的统一接口（PRD §12.1 第 4 条：同一套用例贯穿 V0→V6）。

eval runner 只通过这里的类型与被测对象交互，**不走 HTTP**（PRD §15 Phase 0）。
V0 naive、V1 +Tools … V6 都实现 `AgentUnderTest`，报表曲线才可比。

约定：
- 身份只在 `start_session` 注入一次（`Auth`），之后任何一轮都不再传身份（红线 1）。
- 时间由 runner 注入（`now`，默认 `EVAL_NOW`），被测方不得自行取系统时间，
  否则 seed 的窗口边界会漂移。
- 副作用（是否真的写了 biz.refunds 等）由 runner 查库判定，**不信任**被测方自述。
- 本文件是并行开发的共享接口：修改需同步 V0 实现与 runner，务必先在 HANDOFF 里声明。
- 接口是**同步**的。runner 对并发 confirm 用**线程池**（ThreadPoolExecutor）并发调用同一个
  `AgentSession.confirm()`；内部为 async 的实现（如 LangGraph + async DB）应自持一个事件循环
  并做线程安全，不要在每次调用里 `asyncio.run`。
- 已知待补（不影响 V0）：`Usage` 按轮聚合无法按模型拆分成本，Phase 6 考核成本前改为逐次调用列表；
  检索 top-k 全集缺字段，Phase 2 前加 `retrieved` 以计算 recall@k。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.schema import Auth, ToolFault


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: int | None = None
    anchor: str | None = None


class ToolCall(BaseModel):
    """一次工具调用的记录。`arguments` 里绝不应出现 user_id / tenant_id（FR-208）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    latency_ms: float | None = None


class Usage(BaseModel):
    """本轮全部模型调用的累计用量。字段名与 Anthropic SDK 的 usage 对齐，便于直接相加。"""

    model_config = ConfigDict(extra="forbid")

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    models: list[str] = Field(default_factory=list)  # 本轮用过的 model id，去重后

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            llm_calls=self.llm_calls + other.llm_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            models=sorted(set(self.models) | set(other.models)),
        )


class TurnResult(BaseModel):
    """被测方对一轮输入的完整响应。runner 的所有确定性断言只看这里 + 数据库。"""

    model_config = ConfigDict(extra="forbid")

    reply: str
    decision: DecisionOutcome
    reason_code: ReasonCode
    confidence: Literal["low", "normal"] = "normal"
    citations: list[Citation] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = 0.0
    # 写操作进入待确认 / 待人工时，被测方给出动作标识，runner 用它做 confirm 与查库
    pending_action_id: str | None = None
    # 被测方内部的策略判定（若有），用于 citation-execution consistency 断言
    verdict_policy_id: str | None = None
    verdict_policy_version: int | None = None
    # 供排障的自由字段（prompt 摘要、trace id 等），runner 原样写入 eval_results.raw
    debug: dict[str, Any] = Field(default_factory=dict)


class AgentSession(ABC):
    """一条会话（对应一个 thread_id）。每个 golden 用例开一条新会话。"""

    @abstractmethod
    def send_user(self, text: str, *, faults: list[ToolFault] | None = None) -> TurnResult:
        """处理一轮用户发言。`faults` 为本轮要注入的工具故障；无工具的实现可忽略。"""

    @abstractmethod
    def confirm(self) -> TurnResult:
        """用户确认当前 pending_action。没有 pending_action 时应返回 ANSWER / OK 并在 reply 说明。

        runner 可能对同一 pending_action 重复或并发调用本方法（幂等用例），实现必须能承受。
        """

    def close(self) -> None:  # noqa: B027  默认无需清理
        """释放资源。默认空实现。"""


class AgentUnderTest(ABC):
    """被测系统的工厂。`name` 出现在报表与 eval_runs.version_tag 里，如 "v0-naive"。"""

    name: str

    @abstractmethod
    def start_session(self, auth: Auth, *, now: datetime) -> AgentSession:
        """以给定身份与"当前时刻"开启一条新会话。身份此后不再传递（红线 1）。"""
