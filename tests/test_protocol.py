from datetime import UTC, datetime

from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import AgentSession, AgentUnderTest, TurnResult, Usage
from cs_agent.eval.schema import Auth, ToolFault


class _EchoSession(AgentSession):
    def send_user(self, text: str, *, faults: list[ToolFault] | None = None) -> TurnResult:
        return TurnResult(reply=text, decision=DecisionOutcome.ANSWER, reason_code=ReasonCode.OK)

    def confirm(self) -> TurnResult:
        return TurnResult(
            reply="无待确认动作", decision=DecisionOutcome.ANSWER, reason_code=ReasonCode.OK
        )


class _Echo(AgentUnderTest):
    name = "echo"

    def start_session(self, auth: Auth, *, now: datetime) -> AgentSession:
        return _EchoSession()


def test_minimal_implementation_satisfies_interface() -> None:
    s = _Echo().start_session(Auth(user_id=101), now=datetime(2026, 9, 1, tzinfo=UTC))
    r = s.send_user("你好")
    assert r.decision is DecisionOutcome.ANSWER and r.reply == "你好"
    assert s.confirm().reason_code is ReasonCode.OK
    s.close()


def test_usage_addition() -> None:
    a = Usage(llm_calls=1, input_tokens=10, output_tokens=2, models=["claude-sonnet-5"])
    b = Usage(llm_calls=2, input_tokens=5, cache_read_input_tokens=4, models=["claude-haiku-4-5"])
    c = a + b
    assert (c.llm_calls, c.input_tokens, c.output_tokens, c.cache_read_input_tokens) == (
        3,
        15,
        2,
        4,
    )
    assert c.models == ["claude-haiku-4-5", "claude-sonnet-5"]
