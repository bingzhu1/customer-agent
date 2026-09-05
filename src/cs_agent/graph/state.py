"""图状态（PRD §6.2）。

**LLM 只写 `understanding` 与 `reply`**，其余字段全部由确定性代码填充：
`ownership_ok` 来自 Repository 的查询结果，`verdict` 来自策略引擎，`decision` 来自决策矩阵。
这条分工是红线 2 与 ADR-0005 在代码里的落点。
"""

from __future__ import annotations

from typing import Any, TypedDict

from cs_agent.decision.matrix import Decision
from cs_agent.eval.protocol import Citation, ToolCall, Usage
from cs_agent.graph.llm import Understanding
from cs_agent.memory.case_facts import CaseFacts
from cs_agent.memory.user_memory import MemoryRecord
from cs_agent.policy.engine import PolicyVerdict


class AgentState(TypedDict, total=False):
    """一轮对话的状态。`total=False`：节点只回填自己负责的键。"""

    # 输入
    user_text: str

    # ingest（确定性代码写）
    #: 本会话已确认的事实（PRD §10 ② 层）。只由确定性代码从工具结果与 verdict 填充。
    case_facts: CaseFacts
    #: 长期记忆命中（§10 ④ 层，**非权威**）。只能进 respond 的 prompt，
    #: 绝不进 policy_gate / decide——由 tests/test_memory_wiring.py 反向校验。
    memory_hints: list[MemoryRecord]

    # understand（LLM 写）
    understanding: Understanding

    # act（确定性代码写）
    order: dict[str, Any] | None
    shipping: dict[str, Any] | None
    ticket: dict[str, Any] | None
    policy_hits: list[dict[str, Any]]
    refunds: list[dict[str, Any]]
    payments: list[dict[str, Any]]
    profile: dict[str, Any] | None
    #: 本轮检索的最高相似度与分带；未检索时为 None（决策矩阵据此跳过 τ 门控）
    retrieval_max_score: float | None
    retrieval_band: str | None
    has_citable_chunk: bool
    tool_calls: list[ToolCall]
    #: 请求的实体存在且属于当前用户；查不到（他人或不存在）即为 False
    ownership_ok: bool
    missing_entity: bool
    injection_suspected: bool
    tool_budget_exceeded: bool

    # policy_gate（确定性代码写，V3 起才有）
    verdict: PolicyVerdict | None

    # decide（确定性代码写）
    decision: Decision

    # respond（LLM 写文本，引用由确定性代码给定）
    reply: str
    citations: list[Citation]

    usage: Usage
