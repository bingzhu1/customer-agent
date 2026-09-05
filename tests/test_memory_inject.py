"""记忆注入的非权威标注（FR-708、ADR-0009 配套措施 1）。"""

from __future__ import annotations

from cs_agent.memory.inject import HEADER, HINT_PREFIX, render_hints


class _Mem:
    def __init__(self, key: str, value: str, confidence: float = 0.8) -> None:
        self.mem_key = key
        self.mem_value = value
        self.confidence = confidence


MEMS = [_Mem("channel_preference", "希望短信通知", 0.92), _Mem("language_preference", "中文", 0.75)]


def test_empty_returns_empty_string() -> None:
    assert render_hints([]) == ""


def test_every_line_carries_the_non_authoritative_prefix() -> None:
    body = [ln for ln in render_hints(MEMS).splitlines() if ln not in HEADER.splitlines()]
    assert len(body) == len(MEMS)
    assert all(ln.startswith(HINT_PREFIX) for ln in body)


def test_header_forbids_using_memory_for_authorization() -> None:
    """FR-708 要求标注"可能有误"；ADR-0009 还要求写明不得用于资格 / 权限 / 金额。"""
    out = render_hints(MEMS)
    for phrase in ("可能过时或有误", "不得用于判断资格、权限、金额", "以业务数据为准", "归属"):
        assert phrase in out


def test_values_and_confidence_are_rendered() -> None:
    out = render_hints(MEMS)
    assert "channel_preference：希望短信通知（置信度 0.92）" in out
    assert "language_preference：中文（置信度 0.75）" in out


def test_prefix_is_a_fixed_template_not_model_generated() -> None:
    assert HINT_PREFIX == "[非权威提示，可能过时或有误]"
