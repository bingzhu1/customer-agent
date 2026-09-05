"""回复语言的确定性判定（domain/language.py）：本轮要求 → 记忆偏好 → 中文。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cs_agent.domain.language import (
    detect_requested_language,
    language_from_memory_value,
    resolve_reply_language,
)


@dataclass(frozen=True)
class Hint:
    mem_key: str
    mem_value: str


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("以后用英文回答", "en"),
        ("以后英文说", "en"),
        ("Please answer in English.", "en"),
        ("switch to English", "en"),
        ("以后还是用中文跟我说吧", "zh"),
        ("请用中文回答：你记住了我哪些偏好？", "zh"),
        ("不要英文了，改成中文", "zh"),  # 两种都提到，最后出现的赢
        ("先用中文，算了还是用英文回答", "en"),
        ("订单 82913 我要退款", None),
        ("我买的 English textbook 还没到", None),  # 只提到词，不是要求
        ("", None),
    ],
)
def test_detect_requested_language(text: str, expected: str | None) -> None:
    assert detect_requested_language(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("用户希望用英文沟通", "en"),
        ("用户希望以英文进行沟通", "en"),
        ("User prefers English", "en"),
        ("用户希望客服用中文沟通", "zh"),
        ("用户偏好简短回复", None),
    ],
)
def test_language_from_memory_value(value: str, expected: str | None) -> None:
    assert language_from_memory_value(value) == expected


def test_turn_request_beats_memory() -> None:
    hints = [Hint("language_preference", "用户希望用英文沟通")]
    assert resolve_reply_language("请用中文回答", hints) == "zh"


def test_memory_applies_when_turn_is_silent() -> None:
    hints = [
        Hint("channel_preference", "不要打电话"),
        Hint("language_preference", "用户希望用英文沟通"),
    ]
    assert resolve_reply_language("订单 82923 到哪了", hints) == "en"


def test_other_memory_keys_do_not_set_language() -> None:
    # 别的 key 里哪怕提到 English 也不算语言偏好
    hints = [Hint("communication_style", "用户喜欢在回复里夹 English 单词")]
    assert resolve_reply_language("订单 82923 到哪了", hints) == "zh"


def test_default_is_chinese() -> None:
    assert resolve_reply_language("订单 82923 到哪了", []) == "zh"
