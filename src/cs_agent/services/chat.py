"""会话服务：建会话、跑一轮图、落消息（FR-101/102/104）。

事务边界在这里：一轮对话的"用户消息 + 助手回复 + last_active_at"要么全写要么全不写。
路由层只做参数校验与序列化，不碰这些（CLAUDE.md §7 分层边界）。

图与工具都绑定同一个 `AuthContext`：会话归属、订单归属用的是同一个身份来源，
不存在"路由校验过一次、工具又自己判一次"的双份逻辑。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy.orm import Session

from cs_agent.auth.context import AuthContext
from cs_agent.db.models.agent import Message, Thread
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import Citation, Usage
from cs_agent.graph.build import build_graph
from cs_agent.graph.llm import FallbackLlm, Llm
from cs_agent.graph.nodes import Deps
from cs_agent.graph.state import AgentState
from cs_agent.graph.tools import ToolBelt
from cs_agent.memory.case_facts import CaseFacts
from cs_agent.policy.schema import PolicySet, load_policies
from cs_agent.repositories.agent import ThreadRepository
from cs_agent.repositories.biz import BizRepository
from cs_agent.settings import get_settings

POLICY_DIR = Path(__file__).resolve().parents[3] / "policies"

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

#: 需要给出转人工入口的终态（§9.4 "用户看到"列）。
HANDOFF_OUTCOMES = frozenset({DecisionOutcome.REQUIRE_HUMAN, DecisionOutcome.DEGRADE})
HANDOFF_TEXT = "可以为你转接人工客服。"

_POLICIES: PolicySet | None = None


def get_policies() -> PolicySet:
    """进程级缓存：策略 YAML 每次请求重读没有意义。"""
    global _POLICIES
    if _POLICIES is None:
        _POLICIES = load_policies(POLICY_DIR)
    return _POLICIES


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """一轮对话的结果。字段与 PRD §8.2 的响应体一一对应。"""

    thread_id: UUID
    reply: str
    decision: DecisionOutcome
    reason_code: ReasonCode
    confidence: str
    citations: list[Citation]
    tools_used: list[str]
    handoff_offer: str | None
    usage: Usage
    latency_ms: float


@dataclass(frozen=True, slots=True)
class ThreadView:
    """会话详情：会话本体 + 消息 + CaseFacts 摘要。"""

    thread: Thread
    messages: list[Message]
    case_facts: CaseFacts
    narrative_summary: str | None


class ChatService:
    """一次请求一个实例。`llm` 可注入，便于测试与离线联调。"""

    def __init__(
        self,
        session: Session,
        ctx: AuthContext,
        *,
        llm: Llm | FallbackLlm | None = None,
    ) -> None:
        self._session = session
        self._ctx = ctx
        self._threads = ThreadRepository(session, ctx)
        self._llm = llm

    # --- 查询 -----------------------------------------------------------------

    def create_thread(self) -> Thread:
        thread = self._threads.create_thread()
        self._session.commit()
        return thread

    def get_thread_view(self, thread_id: UUID) -> ThreadView | None:
        """他人会话与不存在的会话同样返回 `None`，由路由翻成 404（FR-104）。"""
        thread = self._threads.get_thread(thread_id)
        if thread is None:
            return None
        state = self._threads.get_case_state(thread_id)
        return ThreadView(
            thread=thread,
            messages=self._threads.list_messages(thread_id),
            # CaseFacts 只由确定性代码写入；这里只读，空的就是空的，不补默认值
            case_facts=CaseFacts.from_json_dict(state.case_facts if state else None),
            narrative_summary=state.narrative_summary if state else None,
        )

    # --- 一轮对话 -------------------------------------------------------------

    def send_message(self, thread_id: UUID, text: str) -> TurnOutcome | None:
        """跑一轮图并落库。会话不属于本人时返回 `None`。"""
        thread = self._threads.get_thread(thread_id)
        if thread is None:
            return None

        started = time.perf_counter()
        now = datetime.now(UTC)
        self._threads.add_message(thread_id, role=ROLE_USER, content=text, now=now)

        state = self._run_graph(text, now)
        decision = state["decision"]
        reply = state.get("reply") or ""

        self._threads.add_message(thread_id, role=ROLE_ASSISTANT, content=reply, now=now)
        # 用户消息、助手回复、last_active_at 在同一个事务里
        self._session.commit()

        return TurnOutcome(
            thread_id=thread_id,
            reply=reply,
            decision=decision.outcome,
            reason_code=decision.reason_code,
            confidence="low"
            if decision.reason_code is ReasonCode.RETRIEVAL_LOW_CONFIDENCE
            else "normal",
            citations=list(state.get("citations") or []),
            tools_used=[call.name for call in state.get("tool_calls") or []],
            handoff_offer=HANDOFF_TEXT if decision.outcome in HANDOFF_OUTCOMES else None,
            usage=state.get("usage") or Usage(),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _run_graph(self, text: str, now: datetime) -> AgentState:
        policies = get_policies()
        belt = ToolBelt(repo=BizRepository(self._session, self._ctx), policies=policies)
        deps = Deps(
            llm=self._ensure_llm(),
            tools=belt,
            policies=policies,
            now=now,
            auth=self._ctx,
            # API 走完整图（等同 V3）：资格判定必须由策略引擎给，不能落到"默认转人工"
            enable_policy_gate=True,
        )
        graph = build_graph(deps)
        raw: dict[str, Any] = graph.invoke(
            {"user_text": text},
            config={"configurable": {"thread_id": str(self._ctx.user_id)}},
        )
        return cast(AgentState, raw)

    def _ensure_llm(self) -> Llm | FallbackLlm:
        if self._llm is None:
            self._llm = Llm() if get_settings().llm_configured else FallbackLlm()
        return self._llm
