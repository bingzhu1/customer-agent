"""图装配（ADR-0003）。

线性图：ingest → understand → act → policy_gate → decide → respond。
没有条件边——**流程本身不该由模型决定**，节点内部的分支都是确定性的。

checkpointer 冲刺阶段用 `MemorySaver`：进程内保存，够跑 eval 与本地联调。
Postgres checkpointer（崩溃恢复、HITL resume 的真正载体）是 Phase 1 的正式内容，
换上去只需要替换这里的 `checkpointer`，节点与状态都不用动。
"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from cs_agent.graph.nodes import Deps, act, decide, ingest, policy_gate, respond, understand
from cs_agent.graph.state import AgentState

NODES = (
    ("ingest", ingest),
    ("understand", understand),
    ("act", act),
    ("policy_gate", policy_gate),
    ("decide", decide),
    ("respond", respond),
)


def build_graph(deps: Deps, *, checkpointer: Any | None = None) -> Any:
    """编译一张图。`deps` 通过 partial 绑进节点，因此身份不经过图状态、也不经过 LLM。"""
    builder: StateGraph[AgentState] = StateGraph(AgentState)
    for name, fn in NODES:
        builder.add_node(name, partial(fn, deps=deps))

    previous = START
    for name, _ in NODES:
        builder.add_edge(previous, name)
        previous = name
    builder.add_edge(previous, END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
