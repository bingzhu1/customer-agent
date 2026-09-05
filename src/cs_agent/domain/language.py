"""回复语言的确定性判定。

只有两个来源，都是确定性代码在判、不经 LLM：

1. 用户**本轮**明确要求（"以后用英文回答" / "please answer in English"），最高优先；
2. 否则看长期记忆里的 `language_preference`（非权威提示，见 ADR-0009）；
3. 都没有就中文。

记忆在这里只决定"用哪种语言说"，不碰任何判定——这是红线 3 允许的"语气 / 沟通方式"范围。
模块不做 IO、不调 LLM。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Literal, Protocol

Lang = Literal["zh", "en"]

DEFAULT_LANG: Lang = "zh"

#: 给 respond 的 prompt 用的可读名字。
LANGUAGE_NAMES: dict[Lang, str] = {"zh": "中文", "en": "English"}

#: 记忆里承载语言偏好的 key（抽取器的五类之一）。
LANGUAGE_MEMORY_KEY = "language_preference"

# 本轮明确要求：要有"用 / 说 / 改成 / 回答"这类动作语境，光提到"English"一个词不算
# （"我买的 English textbook 没到"不该把整段回复切成英文）。
_REQUEST_EN = re.compile(
    r"(?:用|说|讲|改成|换成|切换到?|回答|回复|沟通)\s*(?:英文|英语)"
    r"|(?:英文|英语)\s*(?:回答|回复|说|沟通|交流)"
    r"|\bin\s+English\b|\bspeak\s+English\b|\bEnglish,?\s+please\b|\bswitch\s+to\s+English\b",
    re.IGNORECASE,
)
_REQUEST_ZH = re.compile(
    r"(?:用|说|讲|改成|换成|切换到?|回答|回复|沟通)\s*(?:中文|汉语|普通话)"
    r"|(?:中文|汉语)\s*(?:回答|回复|说|沟通|交流)"
    r"|\bin\s+Chinese\b|\bspeak\s+Chinese\b|\bChinese,?\s+please\b|\bswitch\s+to\s+Chinese\b",
    re.IGNORECASE,
)

# 记忆值是抽取器写的一句陈述（"用户希望用英文沟通"），只看提到哪种语言。
_MENTION_EN = re.compile(r"英文|英语|English", re.IGNORECASE)
_MENTION_ZH = re.compile(r"中文|汉语|Chinese", re.IGNORECASE)


def _last_wins(text: str, en: re.Pattern[str], zh: re.Pattern[str]) -> Lang | None:
    """两种语言都被提到时（"不要英文，改成中文"），以**最后**出现的为准。"""
    en_pos = max((m.end() for m in en.finditer(text)), default=-1)
    zh_pos = max((m.end() for m in zh.finditer(text)), default=-1)
    if en_pos < 0 and zh_pos < 0:
        return None
    return "en" if en_pos > zh_pos else "zh"


def detect_requested_language(text: str) -> Lang | None:
    """用户本轮有没有明确要求某种语言。没有返回 None。"""
    return _last_wins(text or "", _REQUEST_EN, _REQUEST_ZH)


def language_from_memory_value(value: str) -> Lang | None:
    """从 `language_preference` 的陈述里读出语言。读不出返回 None。"""
    return _last_wins(value or "", _MENTION_EN, _MENTION_ZH)


class SupportsLanguageHint(Protocol):
    @property
    def mem_key(self) -> str: ...

    @property
    def mem_value(self) -> str: ...


def language_from_memory(hints: Iterable[SupportsLanguageHint]) -> Lang | None:
    for h in hints:
        if h.mem_key == LANGUAGE_MEMORY_KEY:
            lang = language_from_memory_value(h.mem_value)
            if lang is not None:
                return lang
    return None


def resolve_reply_language(user_text: str, hints: Sequence[SupportsLanguageHint] = ()) -> Lang:
    """本轮要求 → 记忆偏好 → 中文。"""
    return detect_requested_language(user_text) or language_from_memory(hints) or DEFAULT_LANG
