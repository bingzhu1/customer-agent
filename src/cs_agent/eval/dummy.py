"""哑 agent：每轮都转人工。用于打通 runner 全链路，不调用任何 LLM。

它应当只通过"明确要人工"类用例，其余全部失败——这正是它存在的意义：
如果哑 agent 也能大面积通过，说明 golden dataset 不够严。
"""

from __future__ import annotations

from datetime import datetime

from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import AgentSession, AgentUnderTest, ToolCall, TurnResult
from cs_agent.eval.schema import Auth, ToolFault


class _AlwaysHumanSession(AgentSession):
    def send_user(self, text: str, *, faults: list[ToolFault] | None = None) -> TurnResult:
        return TurnResult(
            reply="已为您转接人工客服，请稍候。",
            decision=DecisionOutcome.REQUIRE_HUMAN,
            reason_code=ReasonCode.CUSTOMER_ESCALATION_REQUEST,
            tool_calls=[
                ToolCall(
                    name="escalate_to_human",
                    arguments={"reason_code": ReasonCode.CUSTOMER_ESCALATION_REQUEST.value},
                )
            ],
        )

    def confirm(self) -> TurnResult:
        return TurnResult(
            reply="当前没有待确认的操作。",
            decision=DecisionOutcome.ANSWER,
            reason_code=ReasonCode.OK,
        )


class AlwaysHumanAgent(AgentUnderTest):
    name = "dummy-always-human"

    def start_session(self, auth: Auth, *, now: datetime) -> AgentSession:
        return _AlwaysHumanSession()
