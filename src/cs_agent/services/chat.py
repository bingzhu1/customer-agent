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
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy.orm import Session

from cs_agent.actions import (
    ActionProposal,
    ActionService,
    ActionType,
    ExecutionOutcome,
)
from cs_agent.auth.context import AuthContext
from cs_agent.db.base import get_session_factory
from cs_agent.db.models.agent import Message, Thread
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import Citation, Usage
from cs_agent.graph.build import build_graph
from cs_agent.graph.llm import FallbackLlm, Llm
from cs_agent.graph.memory_store import DbCaseFactsStore
from cs_agent.graph.nodes import Deps
from cs_agent.graph.state import AgentState
from cs_agent.graph.tools import ToolBelt
from cs_agent.memory.case_facts import CaseFacts
from cs_agent.memory.jobs import ExtractionQueue
from cs_agent.memory.user_memory import UserMemoryRepo
from cs_agent.policy.schema import PolicySet, load_policies
from cs_agent.rag.provider import default_provider, default_retriever
from cs_agent.rag.retriever import PolicyRetriever
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
_RETRIEVER: PolicyRetriever | None = None
_MEMORY: UserMemoryRepo | None = None
_QUEUE: ExtractionQueue | None = None


def get_policies() -> PolicySet:
    """进程级缓存：策略 YAML 每次请求重读没有意义。"""
    global _POLICIES
    if _POLICIES is None:
        _POLICIES = load_policies(POLICY_DIR)
    return _POLICIES


def get_retriever() -> PolicyRetriever:
    """进程级缓存：provider 与阈值都不随请求变。"""
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = default_retriever()
    return _RETRIEVER


@dataclass(frozen=True, slots=True)
class PendingActionDraft:
    """待确认动作的草稿：**只是提议，没有落库**（红线 2，Phase 4 才执行）。"""

    type: str
    order_id: int | None
    amount: str | None
    currency: str | None
    policy_id: str | None
    policy_version: int | None
    #: `agent_actions` 的主键。落库失败就不该有值——前端按它是否为空决定按钮能不能点
    action_id: int | None = None
    expires_at: datetime | None = None


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
    pending_action: PendingActionDraft | None
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

        state = self._run_graph(text, thread_id, now)
        decision = state["decision"]
        reply = state.get("reply") or ""

        # 先把待确认动作落库，再提交对话。顺序是有意的：
        # 记不下动作就不能对用户说"请确认"——那是一句兑现不了的承诺，宁可整轮失败。
        # 反过来（先提交对话再 propose）会让用户看到确认卡片却点不动。
        pending = self._propose_pending_action(state, thread_id, now)

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
            pending_action=pending,
            usage=state.get("usage") or Usage(),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    # --- 写路径（PRD §5.3）-----------------------------------------------------

    def _propose_pending_action(
        self, state: AgentState, thread_id: UUID, now: datetime
    ) -> PendingActionDraft | None:
        """决策为 REQUIRE_CONFIRMATION 时把动作落 `agent_actions`，把真实 `action_id` 带出去。

        金额、订单号一律取自业务库查回来的订单，**不取用户说法**；
        `reason` 用机器可读的 reason_code 而不是用户原话——原话一改，幂等键就跟着变，
        同一笔退款会被算成两笔。
        """
        decision = state["decision"]
        if decision.outcome is not DecisionOutcome.REQUIRE_CONFIRMATION:
            return None
        order = state.get("order") or {}
        verdict = state.get("verdict")
        order_id = order.get("order_id")
        amount = order.get("total_amount")
        if order_id is None or amount is None:
            # 没有订单事实就没有可执行的动作，只给用户看摘要，按钮置灰
            return None

        proposal = ActionProposal(
            ActionType.REFUND,
            {
                "order_id": int(order_id),
                "amount": Decimal(str(amount)),
                "reason": decision.reason_code.value,
            },
        )
        record = self._actions().propose(
            self._ctx,
            thread_id,
            proposal,
            outcome=decision.outcome,
            verdict=verdict,
            window_start=refund_window(now),
        )
        return PendingActionDraft(
            type=ActionType.REFUND.value,
            order_id=int(order_id),
            amount=str(amount),
            currency=order.get("currency"),
            policy_id=verdict.policy_id if verdict is not None else None,
            policy_version=verdict.policy_version if verdict is not None else None,
            action_id=record.id,
            expires_at=record.expires_at,
        )

    def confirm_action(self, action_id: int) -> ExecutionOutcome:
        """用户点确认（PRD §5.3 第二段流）。归属、过期、状态三道校验都在 ActionService 里。

        异常原样往上抛，由路由翻成 404 / 410 / 409——这一层不认识 HTTP 状态码。
        """
        return self._actions().confirm(action_id, self._ctx)

    def reject_action(self, action_id: int, note: str | None = None) -> None:
        """用户放弃这次写操作。动作进终态，不产生任何副作用。"""
        self._actions().reject(action_id, self._ctx, note=note)

    def _actions(self) -> ActionService:
        """写路径用**独立的 session 工厂**：动作与审计有自己的事务边界，
        不跟着一轮对话的读写一起回滚。"""
        return ActionService(get_session_factory())

    def _extraction_queue(self) -> ExtractionQueue | None:
        """进程级异步抽取队列。生产路径**绝不调 drain()**——那等于把抽取拖回热路径。"""
        repo = self._memory_repo()
        if repo is None:
            return None
        global _QUEUE
        if _QUEUE is None:
            _QUEUE = ExtractionQueue(repo)
        return _QUEUE

    def _memory_repo(self) -> UserMemoryRepo | None:
        """长期记忆走与 RAG 同一个 embedding provider。关掉记忆时返回 None。"""
        if not get_settings().memory_enabled:
            return None
        global _MEMORY
        if _MEMORY is None:
            _MEMORY = UserMemoryRepo(default_provider())
        return _MEMORY

    def _run_graph(self, text: str, thread_id: UUID, now: datetime) -> AgentState:
        policies = get_policies()
        belt = ToolBelt(
            repo=BizRepository(self._session, self._ctx),
            policies=policies,
            retriever=get_retriever(),
        )
        memory = self._memory_repo()
        deps = Deps(
            llm=self._ensure_llm(),
            tools=belt,
            policies=policies,
            now=now,
            auth=self._ctx,
            # API 走完整图（等同 V3）：资格判定必须由策略引擎给，不能落到"默认转人工"
            enable_policy_gate=True,
            # CaseFacts 落 agent.case_state，会话跨轮、跨进程都能续上
            case_store=DbCaseFactsStore(thread_id),
            memory=memory,
            extraction_queue=self._extraction_queue(),
            thread_uuid=thread_id,
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


def refund_window(now: datetime) -> datetime:
    """幂等键的时间窗口：整点截断。

    同一用户、同一订单、同一金额，一小时之内反复说"我要退款"算同一笔动作，
    回到同一个 `action_id`，而不是在队列里堆出一串待确认的重复退款。

    **已知边界效应，不是 bug**：固定窗口在整点处切开，10:59 与 11:01 落在两个窗口，
    会得到两个 action。这在"防重复退款"这个目标上可以接受——真正兜住金钱副作用的是
    `UNIQUE(idempotency_key)` 加执行前的状态校验，窗口只是用来收敛待确认队列。
    要消掉这个毛刺就得换成滑动窗口（如"最近一小时内已有同参数的待确认动作则复用"），
    代价是查询不再是纯函数。列为升级点，别当 bug 重新发现。
    """
    return now.replace(minute=0, second=0, microsecond=0)


def close_extraction_queue() -> None:
    """进程退出前回收线程池。由 API 的 lifespan 调用。"""
    global _QUEUE
    if _QUEUE is not None:
        _QUEUE.close()
        _QUEUE = None
