"""V0 naive baseline 的单测。

原则：**绝不真调 LLM**。所有用例注入替身 client；另有一条用例断言默认路径下
`anthropic.Anthropic` 根本没有被实例化，防止将来有人不小心让测试联网。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cs_agent.agents.v0_naive import (
    CONFIRM_TEXT,
    SYSTEM_PROMPT,
    V0NaiveAgent,
    V0NaiveSession,
)
from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import AgentSession, AgentUnderTest, TurnResult
from cs_agent.eval.schema import Auth, ToolFault
from cs_agent.seed.reference import EVAL_NOW

AUTH = Auth(user_id=101)


class FakeBlock:
    def __init__(self, text: str, type_: str = "text") -> None:
        self.text = text
        self.type = type_


class FakeUsage:
    def __init__(
        self,
        input_tokens: int = 120,
        output_tokens: int = 30,
        cache_read_input_tokens: int | None = 0,
        cache_creation_input_tokens: int | None = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class FakeMessage:
    def __init__(self, blocks: list[FakeBlock], usage: FakeUsage | None = None) -> None:
        self.content = blocks
        self.usage = usage or FakeUsage()


class FakeMessages:
    def __init__(self, replies: list[FakeMessage]) -> None:
        self._replies = replies
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[index]


class FakeClient:
    def __init__(self, *replies: str | FakeMessage) -> None:
        msgs = [
            r if isinstance(r, FakeMessage) else FakeMessage([FakeBlock(r)]) for r in replies
        ] or [FakeMessage([FakeBlock("好的。")])]
        self.messages = FakeMessages(msgs)


def make_agent(*replies: str | FakeMessage) -> tuple[V0NaiveAgent, FakeClient]:
    client = FakeClient(*replies)
    return V0NaiveAgent(client, model="claude-sonnet-5"), client


def test_implements_protocol_base_classes() -> None:
    """实现了 protocol 的两个抽象基类。"""
    agent, _ = make_agent()
    session = agent.start_session(AUTH, now=EVAL_NOW)
    assert isinstance(agent, AgentUnderTest)
    assert isinstance(session, AgentSession)
    assert agent.name == "v0-naive"


def test_single_turn_maps_to_turn_result() -> None:
    """一轮对话映射成 TurnResult：恒 ANSWER / OK，引用与工具恒空。"""
    agent, client = make_agent("您的订单正在配送中。")
    session = agent.start_session(AUTH, now=EVAL_NOW)

    result = session.send_user("我的订单到哪了？")

    assert isinstance(result, TurnResult)
    assert result.reply == "您的订单正在配送中。"
    # V0 没有决策层：恒 ANSWER / OK
    assert result.decision is DecisionOutcome.ANSWER
    assert result.reason_code is ReasonCode.OK
    assert result.confidence == "normal"
    # 无检索、无工具 → 两者恒空，绝不编造引用
    assert result.citations == []
    assert result.tool_calls == []
    assert result.pending_action_id is None
    assert result.verdict_policy_id is None
    assert len(client.messages.calls) == 1


def test_usage_is_recorded() -> None:
    """usage 如实记录，它是 §12.6 Tokens / Cost 两列的数据来源。"""
    message = FakeMessage(
        [FakeBlock("好的。")],
        FakeUsage(input_tokens=250, output_tokens=64, cache_read_input_tokens=12),
    )
    agent, _ = make_agent(message)
    session = agent.start_session(AUTH, now=EVAL_NOW)

    usage = session.send_user("你好").usage

    assert usage.llm_calls == 1
    assert usage.input_tokens == 250
    assert usage.output_tokens == 64
    assert usage.cache_read_input_tokens == 12
    assert usage.models == ["claude-sonnet-5"]


def test_usage_none_fields_default_to_zero() -> None:
    """SDK 在未启用缓存时把 cache_* 返回为 None，不能让它炸掉或污染统计。"""
    message = FakeMessage(
        [FakeBlock("好的。")],
        FakeUsage(cache_read_input_tokens=None, cache_creation_input_tokens=None),
    )
    agent, _ = make_agent(message)
    session = agent.start_session(AUTH, now=EVAL_NOW)

    usage = session.send_user("你好").usage

    assert usage.cache_read_input_tokens == 0
    assert usage.cache_creation_input_tokens == 0


def test_multi_turn_resends_full_history() -> None:
    """多轮把完整历史原样重发：不压缩、不摘要。"""
    agent, client = make_agent("第一次回复", "第二次回复")
    session = agent.start_session(AUTH, now=EVAL_NOW)

    session.send_user("订单 82913 怎么样了？")
    second = session.send_user("那它多少钱？")

    assert second.reply == "第二次回复"
    sent = client.messages.calls[1]["messages"]
    assert sent == [
        {"role": "user", "content": "订单 82913 怎么样了？"},
        {"role": "assistant", "content": "第一次回复"},
        {"role": "user", "content": "那它多少钱？"},
    ]
    assert client.messages.calls[1]["system"] == SYSTEM_PROMPT


def test_identity_never_enters_prompt() -> None:
    """`Auth` 只用于开会话，user_id 不得出现在任何送给模型的内容里。"""
    agent, client = make_agent("好的。")
    session = agent.start_session(Auth(user_id=101, roles=["customer"]), now=EVAL_NOW)

    session.send_user("查一下我的订单")

    payload = repr(client.messages.calls)
    assert "101" not in payload
    assert "user_id" not in payload
    assert "roles" not in payload


def test_now_never_enters_prompt() -> None:
    """V0 不做日期计算，塞 now 进 prompt 会让基线偷跑。"""
    agent, client = make_agent("好的。")
    session = agent.start_session(AUTH, now=EVAL_NOW)

    session.send_user("退款还来得及吗？")

    payload = repr(client.messages.calls)
    assert "2026" not in payload


def test_tool_faults_are_ignored() -> None:
    """V0 没有工具，protocol 允许忽略 faults；关键是不能抛异常。"""
    agent, _ = make_agent("好的。")
    session = agent.start_session(AUTH, now=EVAL_NOW)

    result = session.send_user(
        "退款政策是多少天？", faults=[ToolFault(tool="search_policy", error="unavailable")]
    )

    assert result.decision is DecisionOutcome.ANSWER


def test_confirm_returns_answer_ok_without_idempotency() -> None:
    """confirm 返回 ANSWER / OK，且不产生幂等语义。"""
    agent, client = make_agent("已收到您的确认。")
    session = agent.start_session(AUTH, now=EVAL_NOW)

    result = session.confirm()

    assert result.decision is DecisionOutcome.ANSWER
    assert result.reason_code is ReasonCode.OK
    # 基线没有幂等机制，绝不会报 IDEMPOTENT_REPLAY —— IDEM 用例应当因此失败
    assert result.reason_code is not ReasonCode.IDEMPOTENT_REPLAY
    assert client.messages.calls[0]["messages"][-1] == {"role": "user", "content": CONFIRM_TEXT}


def test_concurrent_confirm_keeps_history_intact() -> None:
    """protocol 要求实现能承受并发确认（IDEM-002 用线程池并发调用）。"""
    agent, client = make_agent(*[f"回复{i}" for i in range(8)])
    session = agent.start_session(AUTH, now=EVAL_NOW)
    session.send_user("订单 82913 我想退款")

    results: list[TurnResult] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(session.confirm())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(results) == 4
    # 每轮追加 user + assistant 各一条，5 轮 → 10 条，无交错丢失
    assert len(session._messages) == 10
    assert len(client.messages.calls) == 5


def test_text_blocks_are_joined_and_non_text_ignored() -> None:
    """多个 text block 拼接，忽略 thinking 等非文本块。"""
    message = FakeMessage(
        [
            FakeBlock("正在为您", "text"),
            FakeBlock("<思考内容>", "thinking"),
            FakeBlock("查询订单。", "text"),
        ]
    )
    agent, _ = make_agent(message)
    session = agent.start_session(AUTH, now=EVAL_NOW)

    assert session.send_user("你好").reply == "正在为您查询订单。"


def test_empty_reply_does_not_raise() -> None:
    """空回复不抛异常。"""
    agent, _ = make_agent(FakeMessage([]))
    session = agent.start_session(AUTH, now=EVAL_NOW)

    assert session.send_user("你好").reply == ""


def test_sessions_do_not_share_history() -> None:
    """两条会话互不共享历史。"""
    agent, client = make_agent("回复")
    first = agent.start_session(AUTH, now=EVAL_NOW)
    second = agent.start_session(Auth(user_id=202), now=EVAL_NOW)

    first.send_user("第一条会话的话")
    second.send_user("第二条会话的话")

    assert client.messages.calls[1]["messages"] == [{"role": "user", "content": "第二条会话的话"}]


def test_default_path_never_builds_real_client() -> None:
    """构造 agent 不应触网、不应要求 API key —— registry 会无参构造它。"""
    import anthropic

    created: list[Any] = []
    original = anthropic.Anthropic

    class Tripwire(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append(kwargs)
            raise AssertionError("测试期间不得创建真实 Anthropic 客户端")

    anthropic.Anthropic = Tripwire  # type: ignore[misc]
    try:
        agent = V0NaiveAgent()  # 无参构造：不得触发客户端创建
        assert created == []
        assert agent.model == "claude-sonnet-5"
        with pytest.raises(AssertionError):
            agent.start_session(AUTH, now=EVAL_NOW)  # 直到真正要用才创建
    finally:
        anthropic.Anthropic = original  # type: ignore[misc]


def test_registry_discovers_v0() -> None:
    """registry 能按约定发现 V0（模块级 AGENT 变量）。"""
    from cs_agent.eval.registry import available_agents, build_agent

    assert "v0" in available_agents()
    agent = build_agent("v0")
    assert isinstance(agent, V0NaiveAgent)
    assert agent.name == "v0-naive"


def test_accepts_now_other_than_eval_now() -> None:
    """可用非 EVAL_NOW 的时刻开会话。"""
    agent, _ = make_agent("好的。")
    session = agent.start_session(AUTH, now=datetime(2030, 1, 1, tzinfo=UTC))
    assert isinstance(session, V0NaiveSession)


def test_runs_through_real_runner_without_network() -> None:
    """把 V0 塞进真实 runner 跑完整用例：接口是否真的对得上，只有这样才验得到。

    用 NullSideEffectProbe 免数据库、用替身 client 免网络。
    断言的是"跑得通、结果被采集到"，不是"通过"——V0 本来就该在 SEC 用例上失败。
    """
    from cs_agent.eval.runner import run_case
    from cs_agent.eval.schema import load_golden_file
    from cs_agent.eval.side_effects import NullSideEffectProbe

    cases = load_golden_file(Path(__file__).resolve().parents[1] / "data/golden/security.yaml")
    case = next(c for c in cases if c.id == "SEC-001")

    agent, client = make_agent(*[f"回复{i}" for i in range(len(case.turns))])
    result = run_case(agent, case, NullSideEffectProbe(), now=EVAL_NOW)

    assert result.error is None, result.error
    assert len(result.turns) == len(case.turns)
    assert len(client.messages.calls) == len(case.turns)
    assert result.usage.llm_calls == len(case.turns)
    assert result.usage.models == ["claude-sonnet-5"]
    # V0 恒 ANSWER，而 SEC-001 要求 DENY / OWNERSHIP_MISMATCH → 必然失败，这正是基线的意义
    assert result.passed is False
    assert "decision" in {c.name for c in result.failed_checks()}
