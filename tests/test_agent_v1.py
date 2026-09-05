"""V1 agent 接 `AgentUnderTest` 的契约测试。用假 LLM，不打网络。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from cs_agent.agents.v1_tools import V1ToolsAgent
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import AgentUnderTest, Usage
from cs_agent.eval.schema import Auth
from cs_agent.graph.llm import Understanding
from cs_agent.rag.embeddings import FakeEmbeddings
from cs_agent.rag.ingest import ingest_policies
from cs_agent.seed.biz_seed import run_seed
from cs_agent.seed.reference import EVAL_NOW
from cs_agent.settings import get_settings


class ScriptedLlm:
    """按用户消息里的订单号返回预置理解结果。"""

    model = "fake"

    def understand(self, text: str) -> tuple[Understanding, Usage]:
        import re

        order = re.search(r"订单\s*(\d+)", text)
        ticket = re.search(r"工单\s*(\d+)", text)
        intent = "refund_request" if "退款" in text else "order_status"
        if ticket:
            intent = "ticket_status"
        return (
            Understanding(
                intent=intent,  # type: ignore[arg-type]
                order_id=int(order.group(1)) if order else None,
                ticket_id=int(ticket.group(1)) if ticket else None,
            ),
            Usage(llm_calls=1, input_tokens=10, output_tokens=5, models=["fake"]),
        )

    def respond(self, prompt: str) -> tuple[str, Usage]:
        return "已处理。", Usage(llm_calls=1, input_tokens=20, output_tokens=8, models=["fake"])


@pytest.fixture(scope="module")
def seeded() -> None:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    run_seed(engine)
    ingest_policies(provider=FakeEmbeddings(), engine=engine)


@pytest.fixture
def agent(seeded: None) -> V1ToolsAgent:
    return V1ToolsAgent(llm=ScriptedLlm())  # type: ignore[arg-type]


def test_implements_protocol(agent: V1ToolsAgent) -> None:
    assert isinstance(agent, AgentUnderTest)
    assert agent.name == "v1-tools"


def test_foreign_order_denied_and_usage_accumulated(agent: V1ToolsAgent) -> None:
    session = agent.start_session(Auth(user_id=101), now=EVAL_NOW)
    result = session.send_user("帮我查一下订单 90210 的状态。")
    session.close()

    assert result.decision is DecisionOutcome.DENY
    assert result.reason_code is ReasonCode.OWNERSHIP_MISMATCH
    assert [c.name for c in result.tool_calls] == ["get_order"]
    # 拒绝话术走确定性模板（FR-407），respond 不再调模型，所以本轮只有 understand 一次
    assert result.usage.llm_calls == 1
    assert result.debug["rule_no"] == "1"


def test_tool_arguments_never_carry_identity(agent: V1ToolsAgent) -> None:
    session = agent.start_session(Auth(user_id=101), now=EVAL_NOW)
    result = session.send_user("订单 82913 现在什么状态？")
    session.close()
    for call in result.tool_calls:
        assert not set(call.arguments) & {"user_id", "tenant_id"}


def test_refund_without_policy_engine_escalates(agent: V1ToolsAgent) -> None:
    """V1 没有策略引擎：资格判定转人工，绝不默认放行。"""
    session = agent.start_session(Auth(user_id=101), now=EVAL_NOW)
    result = session.send_user("订单 82913 我要退款。")
    session.close()
    assert result.decision is DecisionOutcome.REQUIRE_HUMAN
    assert result.pending_action_id is None


def test_confirm_executes_nothing(agent: V1ToolsAgent) -> None:
    """V1 没有写路径：confirm 返回 ANSWER / OK 并说明，不产生任何副作用。"""
    session = agent.start_session(Auth(user_id=101), now=EVAL_NOW)
    result = session.confirm()
    session.close()
    assert result.decision is DecisionOutcome.ANSWER
    assert result.reason_code is ReasonCode.OK


def test_sessions_are_isolated(agent: V1ToolsAgent) -> None:
    """不同会话各自独立：101 查不到的单，202 能查到。"""
    s1 = agent.start_session(Auth(user_id=101), now=EVAL_NOW)
    s2 = agent.start_session(Auth(user_id=202), now=EVAL_NOW)
    r1 = s1.send_user("订单 90210 现在什么状态？")
    r2 = s2.send_user("订单 90210 现在什么状态？")
    s1.close()
    s2.close()
    assert r1.reason_code is ReasonCode.OWNERSHIP_MISMATCH
    assert r2.decision is not DecisionOutcome.DENY
