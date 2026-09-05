"""V1 +Tools：LangGraph 最小图 + 4 个只读工具 + 服务端身份（PRD §12.6 V1 行）。

相对 V0 的增量只有三样，报表上的差异应当**只能**归因于它们：

1. 工具能查到真实业务数据（V0 全靠猜）；
2. 身份由 `AuthContext` 注入 Repository，越权在语法上不可表达（ADR-0008）——
   这一项负责把 authorization violation 打到 0；
3. 决策由 `decision.matrix` 产生，不再是恒定 ANSWER。

V1 **没有**策略引擎：涉及退款资格的问题拿不到 `PolicyVerdict`，
矩阵规则 9 会把它们转人工。安全但不好用——这正是 V3 要补的那一格。

写路径同样没有：`confirm()` 不执行任何写操作（Phase 4 才做），
因此幂等类用例在 V1 必然失败，这是如实的水位。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session

from cs_agent.auth.context import AuthContext, Role
from cs_agent.db.base import get_session_factory
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import AgentSession, AgentUnderTest, TurnResult, Usage
from cs_agent.eval.schema import Auth, ToolFault
from cs_agent.graph.build import build_graph
from cs_agent.graph.llm import FallbackLlm, Llm
from cs_agent.graph.nodes import Deps
from cs_agent.graph.state import AgentState
from cs_agent.graph.tools import ToolBelt
from cs_agent.policy.schema import PolicySet, load_policies
from cs_agent.repositories.biz import BizRepository
from cs_agent.settings import get_settings

POLICY_DIR = Path(__file__).resolve().parents[3] / "policies"

CONFIRM_REPLY = "当前没有待确认的操作。退款执行路径尚未开放，如需处理请转人工。"


def _load_policies() -> PolicySet:
    return load_policies(POLICY_DIR)


class GraphSession(AgentSession):
    """一条会话 = 一个 thread_id + 一个数据库会话 + 一张编译好的图。

    `_lock` 保证并发 `confirm()` 安全：SQLAlchemy 的 `Session` 不是线程安全的，
    protocol 又允许 runner 用线程池并发调用同一个会话（幂等用例）。
    """

    def __init__(
        self,
        *,
        db: Session,
        auth: AuthContext,
        now: datetime,
        policies: PolicySet,
        llm: Llm | FallbackLlm,
        enable_policy_gate: bool,
        thread_id: str,
    ) -> None:
        self._db = db
        self._belt = ToolBelt(repo=BizRepository(db, auth), policies=policies)
        self._deps = Deps(
            llm=llm,
            tools=self._belt,
            policies=policies,
            now=now,
            enable_policy_gate=enable_policy_gate,
        )
        self._graph = build_graph(self._deps)
        self._thread_id = thread_id
        self._lock = threading.Lock()

    def send_user(self, text: str, *, faults: list[ToolFault] | None = None) -> TurnResult:
        """跑一轮图。`faults`（工具故障注入）本版未实现，按 protocol 约定忽略。"""
        with self._lock:
            try:
                state = cast(
                    AgentState,
                    self._graph.invoke(
                        {"user_text": text},
                        config={"configurable": {"thread_id": self._thread_id}},
                    ),
                )
            except Exception as exc:  # pragma: no cover - 依赖故障路径
                # 优雅降级：出错就说不知道并转人工，绝不编造（PRD §13.2）
                self._db.rollback()
                return TurnResult(
                    reply="系统暂时无法处理这个请求，已为你转人工。",
                    decision=DecisionOutcome.DEGRADE,
                    reason_code=ReasonCode.DEPENDENCY_UNAVAILABLE,
                    debug={"error": exc.__class__.__name__},
                )
            return _to_turn_result(state)

    def confirm(self) -> TurnResult:
        """V1 没有写路径：不执行、不落库，如实说明并返回 ANSWER / OK（protocol 约定）。"""
        with self._lock:
            return TurnResult(
                reply=CONFIRM_REPLY,
                decision=DecisionOutcome.ANSWER,
                reason_code=ReasonCode.OK,
                debug={"note": "write path not implemented in V1"},
            )

    def close(self) -> None:
        self._db.close()


def _to_turn_result(state: AgentState) -> TurnResult:
    """图状态 → TurnResult。只做搬运，不在这里补任何判断。"""
    decision = state["decision"]
    verdict = state.get("verdict")
    understanding = state.get("understanding")

    pending_action_id = None
    if decision.outcome is DecisionOutcome.REQUIRE_CONFIRMATION:
        # 只是一个内存里的提议标识：V1 不写 agent_actions，也不执行（Phase 4 才有）
        order_id = understanding.order_id if understanding is not None else None
        pending_action_id = f"proposal-{order_id}"

    return TurnResult(
        reply=state.get("reply") or "",
        decision=decision.outcome,
        reason_code=decision.reason_code,
        citations=list(state.get("citations") or []),
        tool_calls=list(state.get("tool_calls") or []),
        usage=state.get("usage") or Usage(),
        pending_action_id=pending_action_id,
        verdict_policy_id=verdict.policy_id if verdict is not None else None,
        verdict_policy_version=verdict.policy_version if verdict is not None else None,
        debug={
            "rule_no": decision.rule_no,
            "intent": understanding.intent if understanding is not None else None,
            "ownership_ok": state.get("ownership_ok"),
            "injection_suspected": state.get("injection_suspected"),
        },
    )


class GraphAgent(AgentUnderTest):
    """图版被测系统的公共工厂。V3 只是把 `enable_policy_gate` 打开。"""

    name = "graph"
    enable_policy_gate = False

    def __init__(
        self,
        llm: Llm | FallbackLlm | None = None,
        *,
        policies: PolicySet | None = None,
    ) -> None:
        self._llm = llm
        self._policies = policies
        self._session_seq = 0

    def _ensure_llm(self) -> Llm | FallbackLlm:
        if self._llm is None:
            settings = get_settings()
            # 没配 key 时退到本地替身，保证测试与离线联调不打网络
            self._llm = Llm() if settings.llm_configured else FallbackLlm()
        return self._llm

    def _ensure_policies(self) -> PolicySet:
        if self._policies is None:
            self._policies = _load_policies()
        return self._policies

    def start_session(self, auth: Auth, *, now: datetime) -> AgentSession:
        """身份在这里注入一次，之后不再传递（红线 1）。"""
        self._session_seq += 1
        ctx = AuthContext.of(auth.user_id, [Role(r) for r in auth.roles])
        return GraphSession(
            db=get_session_factory()(),
            auth=ctx,
            now=now,
            policies=self._ensure_policies(),
            llm=self._ensure_llm(),
            enable_policy_gate=self.enable_policy_gate,
            thread_id=f"{self.name}-{self._session_seq}",
        )


class V1ToolsAgent(GraphAgent):
    """V1：有工具、有身份、有决策矩阵，**没有**策略引擎与写路径。"""

    name = "v1-tools"
    enable_policy_gate = False


AGENT = V1ToolsAgent
