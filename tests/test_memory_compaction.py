"""叙述压缩（FR-703、PRD §10.1 的 2b→2c，不变式 3）。不触网，summarizer 全用替身。"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from cs_agent.memory.compaction import (
    DEFAULT_KEEP_RECENT,
    MAX_SUMMARY_CHARS,
    SUMMARY_MODEL,
    SUMMARY_SCHEMA,
    AnthropicTokenCounter,
    ConversationWindow,
    EstimatingTokenCounter,
    LlmSummarizer,
    Message,
    compact,
    count_window_tokens,
    render_transcript,
    should_compact,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "cs_agent" / "memory" / "compaction.py"


def window(n: int, *, summary: str | None = None, chars: int = 20) -> ConversationWindow:
    """n 条消息，每条 `chars` 个中文字（估算下即 chars 个 token）。"""
    return ConversationWindow(
        messages=tuple(
            Message(role="user" if i % 2 == 0 else "assistant", text=f"第{i}轮" + "话" * chars)
            for i in range(n)
        ),
        narrative_summary=summary,
    )


class _Summarizer:
    def __init__(self, text: str = "用户咨询了退款，情绪平静，问题尚未解决。") -> None:
        self.text = text
        self.calls: list[tuple[str | None, int]] = []

    def summarize(self, previous_summary: str | None, messages: Any) -> str:
        self.calls.append((previous_summary, len(messages)))
        return self.text


class _Exploding:
    def summarize(self, previous_summary: str | None, messages: Any) -> str:
        raise RuntimeError("模型挂了")


# --- 不变式 3：CaseFacts 结构上进不来 ---------------------------------------


def test_compact_signature_has_no_case_facts() -> None:
    """FR-703 的结构保证：压缩函数根本拿不到 CaseFacts，也就压不掉它。"""
    params = inspect.signature(compact).parameters
    assert set(params) == {"window", "summarizer", "threshold", "keep_recent", "counter"}
    rendered = str(inspect.signature(compact)).lower()
    assert "casefacts" not in rendered and "pending" not in rendered


def test_conversation_window_holds_only_2b_and_2c() -> None:
    assert set(ConversationWindow.model_fields) == {"messages", "narrative_summary"}


def test_module_does_not_import_case_facts() -> None:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any("case_facts" in m or "case_state" in m for m in imported)
    assert not any(m.startswith(("cs_agent.policy", "cs_agent.decision")) for m in imported)


# --- 阈值触发 ---------------------------------------------------------------


def test_below_threshold_is_untouched() -> None:
    w = window(4)
    result = compact(w, _Summarizer(), threshold=10_000)
    assert result.compacted is False
    assert result.window == w
    assert result.reason == "below_threshold"


def test_exactly_at_threshold_does_not_trigger() -> None:
    """严格超过才压——边界上反复触发只会白烧 token。"""
    w = window(4)
    exact = count_window_tokens(w)
    assert should_compact(w, threshold=exact) is False
    assert should_compact(w, threshold=exact - 1) is True


def test_over_threshold_compresses_older_messages() -> None:
    w = window(20)
    summarizer = _Summarizer()
    result = compact(w, summarizer, threshold=50, keep_recent=6)
    assert result.compacted is True
    assert len(result.window.messages) == 6
    assert result.window.messages == w.messages[-6:], "保留的必须是最近的，不是最早的"
    assert result.window.narrative_summary == summarizer.text
    assert result.tokens_after < result.tokens_before
    assert result.tokens_saved > 0


def test_only_older_messages_go_to_the_summarizer() -> None:
    summarizer = _Summarizer()
    compact(window(20), summarizer, threshold=50, keep_recent=6)
    assert summarizer.calls == [(None, 14)]


def test_previous_summary_is_folded_in_not_appended() -> None:
    summarizer = _Summarizer("合并后的新摘要")
    result = compact(window(20, summary="上一次的摘要"), summarizer, threshold=50, keep_recent=4)
    assert summarizer.calls[0][0] == "上一次的摘要"
    assert result.window.narrative_summary == "合并后的新摘要"


def test_nothing_older_than_keep_recent(_: None = None) -> None:
    """最近 N 条自己就超阈值时不压，交给上层调 keep_recent，而不是硬删最近的消息。"""
    w = window(4)
    result = compact(w, _Summarizer(), threshold=1, keep_recent=DEFAULT_KEEP_RECENT)
    assert result.compacted is False
    assert result.reason == "nothing_to_compress"
    assert result.window == w


def test_keep_recent_zero_compresses_everything() -> None:
    result = compact(window(6), _Summarizer(), threshold=1, keep_recent=0)
    assert result.compacted is True
    assert result.window.messages == ()


def test_negative_keep_recent_is_rejected() -> None:
    with pytest.raises(ValueError):
        compact(window(6), _Summarizer(), keep_recent=-1)


# --- 安全约束 1：失败绝不丢消息 ---------------------------------------------


@pytest.mark.parametrize("summarizer", [_Exploding(), _Summarizer(""), _Summarizer("   ")])
def test_summarizer_failure_keeps_every_message(summarizer: Any) -> None:
    w = window(20)
    result = compact(w, summarizer, threshold=50, keep_recent=6)
    assert result.compacted is False
    assert result.window == w, "宁可这轮超阈值，也不能删了消息却没换来摘要"
    assert result.reason == "summarizer_failed"


def test_summary_is_capped_and_single_line() -> None:
    result = compact(window(20), _Summarizer("长" * 5000), threshold=50, keep_recent=2)
    summary = result.window.narrative_summary or ""
    assert len(summary) <= MAX_SUMMARY_CHARS
    assert "\n" not in summary


# --- token 计数 -------------------------------------------------------------


def test_estimator_is_deterministic_and_offline() -> None:
    c = EstimatingTokenCounter()
    assert c.count("这是一段中文") == c.count("这是一段中文") == 6
    assert c.count("a" * 80) == 20
    assert c.count("") == 0


def test_window_tokens_include_summary_and_messages() -> None:
    bare = count_window_tokens(window(2))
    with_summary = count_window_tokens(window(2, summary="摘" * 50))
    assert with_summary - bare == 50


def test_anthropic_counter_uses_count_tokens_api() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.messages = self

        def count_tokens(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return type("R", (), {"input_tokens": 123})()

    client = _Client()
    assert AnthropicTokenCounter(client).count("你好") == 123
    assert client.calls[0]["model"] == SUMMARY_MODEL


def test_anthropic_counter_falls_back_when_api_fails() -> None:
    class _Broken:
        def __init__(self) -> None:
            self.messages = self

        def count_tokens(self, **kwargs: Any) -> Any:
            raise RuntimeError("网络挂了")

    assert AnthropicTokenCounter(_Broken()).count("这是一段中文") == 6


def test_injected_counter_drives_the_decision() -> None:
    class _Always:
        def count(self, text: str) -> int:
            return 999_999

    assert should_compact(window(1), threshold=1000, counter=_Always()) is True


# --- LlmSummarizer 请求形状 -------------------------------------------------


class _FakeClient:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        text = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        block = type("Block", (), {"type": "text", "text": text})()
        return type("Message", (), {"content": [block]})()


def test_llm_summarizer_uses_haiku_structured_output_without_prefill() -> None:
    client = _FakeClient({"summary": "用户咨询退款"})
    out = LlmSummarizer(client=client).summarize(None, window(4).messages)
    assert out == "用户咨询退款"
    call = client.calls[0]
    assert call["model"] == SUMMARY_MODEL == "claude-haiku-4-5"
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}}
    assert "effort" not in call["output_config"]  # Haiku 4.5 不支持
    assert [m["role"] for m in call["messages"]] == ["user"]


def test_llm_summarizer_passes_previous_summary() -> None:
    client = _FakeClient({"summary": "x"})
    LlmSummarizer(client=client).summarize("旧摘要", window(2).messages)
    assert "已有摘要：\n旧摘要" in client.calls[0]["messages"][0]["content"]


def test_llm_summarizer_errors_propagate_to_compact_and_are_absorbed_there() -> None:
    """摘要器自己可以抛；由 compact 统一兜住并保住消息。"""
    summarizer = LlmSummarizer(client=_FakeClient("这不是 JSON"))
    with pytest.raises(json.JSONDecodeError):
        summarizer.summarize(None, window(2).messages)
    w = window(20)
    assert compact(w, summarizer, threshold=50).window == w


def test_summary_prompt_forbids_verdicts_and_fabrication() -> None:
    from cs_agent.memory.compaction import SUMMARY_SYSTEM

    for phrase in ("不要下结论", "不要编造", "摘要不是事实来源"):
        assert phrase in SUMMARY_SYSTEM


def test_render_transcript_is_stable() -> None:
    msgs = (Message(role="user", text="你好"), Message(role="assistant", text="您好"))
    assert render_transcript(msgs) == "用户：你好\n客服：您好"
