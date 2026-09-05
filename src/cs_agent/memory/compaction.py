"""叙述压缩（FR-703、PRD §10.1 的 2b→2c，不变式 3）。

会话状态分三块：

```
2a. CaseFacts   强类型，确定性代码填充   —— 永不压缩
2b. 近期消息    原始对话，最近 N 轮
2c. 叙述摘要    LLM 压缩，只压叙述       ←—— 本模块负责 2b → 2c
```

**不变式 3 在这里是靠签名保证的，不是靠自觉**：`compact()` 根本不接受 `CaseFacts`
参数，本模块也不 import `case_facts`。压缩逻辑再怎么写，都碰不到事实与
`pending_action`——和 ADR-0008 / ADR-0009 同一个思路：让错误不可表达。

两条安全约束：

1. **摘要失败绝不丢消息。** summarizer 抛异常或返回空，`compact()` 原样返回输入窗口。
   宁可这一轮超阈值，也不能把消息删了却没换来摘要——那是不可逆的上下文丢失。
2. **摘要不是事实来源。** 摘要里出现的订单号、金额只是叙述；判定要用的数值一律从
   `CaseFacts` 或业务库重新取（不变式 1）。

阈值默认值放在本模块，**接图时应挪进 `Settings`**（需要改 `settings.py`，不属于本包范围）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

#: 触发压缩的 token 阈值。Sonnet 5 的窗口远大于此——阈值小是为了控成本与延迟，不是怕溢出。
DEFAULT_TOKEN_THRESHOLD = 8_000
#: 压缩后保留的原始消息条数。留够最近几轮，模型才能接住"刚才说的那个"。
DEFAULT_KEEP_RECENT = 6

SUMMARY_MODEL = "claude-haiku-4-5"
SUMMARY_MAX_TOKENS = 1024
#: 摘要长度上限。摘要要是能长过原文，压缩就没意义了。
MAX_SUMMARY_CHARS = 1200

_CJK_RE = re.compile(r"[㐀-鿿　-〿＀-￯]")
_WHITESPACE_RE = re.compile(r"\s+")


class Message(BaseModel):
    """一条原始消息。只有角色与文本——压缩层不需要知道是谁在说话。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user", "assistant"]
    text: str


class ConversationWindow(BaseModel):
    """2b + 2c。**刻意不含 2a**：CaseFacts 不进这个结构，也就不可能被压掉。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[Message, ...] = ()
    narrative_summary: str | None = None


class CompactionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window: ConversationWindow
    compacted: bool
    tokens_before: int
    tokens_after: int
    #: 未压缩时说明原因：`below_threshold` / `nothing_to_compress` / `summarizer_failed`。
    reason: str = ""

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


class TokenCounter(Protocol):
    """token 计数。默认实现是估算；要精确值就注入一个走 `messages.count_tokens` 的实现。"""

    def count(self, text: str) -> int: ...


class EstimatingTokenCounter:
    """离线估算，不发网络请求。

    为什么不默认用 `messages.count_tokens`：压缩判断每轮都要做一次，
    为了一个"要不要压"的布尔值多打一次网络往返，延迟与失败面都不划算。

    估算规则：中日韩字符按 1 token/字，其余按 4 字符/token。
    对中文这是**故意偏高**的——宁可早压一轮，也不要因为低估而超预算。
    """

    def count(self, text: str) -> int:
        cjk = len(_CJK_RE.findall(text))
        rest = len(text) - cjk
        return cjk + (rest + 3) // 4


class AnthropicTokenCounter:
    """精确计数，走 `client.messages.count_tokens`。评估与容量规划时用它。

    失败时回落到估算：计数本身失败不该让压缩判断炸掉。
    """

    def __init__(self, client: Any, *, model: str = SUMMARY_MODEL) -> None:
        self._client = client
        self._model = model
        self._fallback = EstimatingTokenCounter()

    def count(self, text: str) -> int:
        if not text:
            return 0
        try:
            response = self._client.messages.count_tokens(
                model=self._model, messages=[{"role": "user", "content": text}]
            )
            return int(response.input_tokens)
        except Exception:  # noqa: BLE001  计数失败退回估算，不影响主流程
            return self._fallback.count(text)


class NarrativeSummarizer(Protocol):
    """把"更早的消息 + 已有摘要"压成一段新的叙述摘要。"""

    def summarize(self, previous_summary: str | None, messages: Sequence[Message]) -> str: ...


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

SUMMARY_SYSTEM = """你把一段客服对话压缩成简短的叙述摘要，供后续轮次参考。

要保留的：用户的诉求、情绪与语气、已经沟通过的内容、还没解决的问题。
要丢掉的：寒暄、重复、与本次诉求无关的闲聊。

铁律：
- 只概括已经发生的对话，不要下结论，不要判断能不能退款、要不要审批。
- 不要编造对话里没有的内容；不确定的就不写。
- 摘要不是事实来源。订单号与金额的权威值在业务数据里，摘要里写到它们只是叙述。
- 已有摘要与新消息合并成一段连贯的叙述，不要写成两段拼接。
- 中文，300 字以内。"""


class _MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class LlmClient(Protocol):
    """只用到 `client.messages.create(...)`，收窄成这个形状便于测试注入替身。"""

    @property
    def messages(self) -> _MessagesAPI: ...


def _text_of(message: Any) -> str:
    parts = [
        block.text
        for block in getattr(message, "content", [])
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts).strip()


def render_transcript(messages: Sequence[Message]) -> str:
    speaker = {"user": "用户", "assistant": "客服"}
    return "\n".join(f"{speaker[m.role]}：{m.text}" for m in messages)


class LlmSummarizer:
    """`claude-haiku-4-5` + `output_config.format`（无 prefill；Haiku 4.5 不支持 effort）。

    摘要是后台整理，不是给用户看的回复，用降级档模型足够。
    """

    def __init__(self, *, client: LlmClient | None = None, model: str = SUMMARY_MODEL) -> None:
        self._client = client
        self._model = model

    def _ensure_client(self) -> LlmClient:
        if self._client is None:
            from anthropic import Anthropic  # 惰性导入：没配 key 也能 import 本模块

            from cs_agent.settings import get_settings

            self._client = cast(
                LlmClient, Anthropic(api_key=get_settings().anthropic_api_key or None)
            )
        return self._client

    def summarize(self, previous_summary: str | None, messages: Sequence[Message]) -> str:
        prompt = render_transcript(messages)
        if previous_summary:
            prompt = f"已有摘要：\n{previous_summary}\n\n新增对话：\n{prompt}"
        message = self._ensure_client().messages.create(
            model=self._model,
            max_tokens=SUMMARY_MAX_TOKENS,
            system=SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        )
        payload = json.loads(_text_of(message))
        if not isinstance(payload, dict):
            raise ValueError("摘要返回的不是对象")
        return str(payload.get("summary", ""))


def window_text(window: ConversationWindow) -> str:
    """参与阈值判断的全部文本 = 已有摘要 + 全部原始消息。"""
    parts = [window.narrative_summary or "", render_transcript(window.messages)]
    return "\n".join(p for p in parts if p)


def count_window_tokens(window: ConversationWindow, counter: TokenCounter | None = None) -> int:
    return (counter or EstimatingTokenCounter()).count(window_text(window))


def should_compact(
    window: ConversationWindow,
    *,
    threshold: int = DEFAULT_TOKEN_THRESHOLD,
    counter: TokenCounter | None = None,
) -> bool:
    """纯谓词，便于不带 LLM 地测阈值边界。恰好等于阈值不触发（严格超过才压）。"""
    return count_window_tokens(window, counter) > threshold


def _clean(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.replace("\n", " ")).strip()[:MAX_SUMMARY_CHARS]


def compact(
    window: ConversationWindow,
    summarizer: NarrativeSummarizer,
    *,
    threshold: int = DEFAULT_TOKEN_THRESHOLD,
    keep_recent: int = DEFAULT_KEEP_RECENT,
    counter: TokenCounter | None = None,
) -> CompactionResult:
    """超阈值时把较早的消息压进叙述摘要，保留最近 `keep_recent` 条原始消息。

    **签名里没有 `CaseFacts`**——这是不变式 3 的结构保证，不是约定（FR-703）。
    摘要失败时原样返回，绝不丢消息。
    """
    if keep_recent < 0:
        raise ValueError(f"keep_recent 不能为负，收到 {keep_recent}")
    counter = counter or EstimatingTokenCounter()
    before = count_window_tokens(window, counter)

    if before <= threshold:
        return CompactionResult(
            window=window,
            compacted=False,
            tokens_before=before,
            tokens_after=before,
            reason="below_threshold",
        )

    to_compress = window.messages[: max(0, len(window.messages) - keep_recent)]
    if not to_compress:
        # 最近 N 条自己就超了阈值：没有可压的更早消息，交给上层决定要不要调小 keep_recent
        return CompactionResult(
            window=window,
            compacted=False,
            tokens_before=before,
            tokens_after=before,
            reason="nothing_to_compress",
        )

    try:
        summary = _clean(summarizer.summarize(window.narrative_summary, to_compress))
    except Exception:  # noqa: BLE001  压缩失败不得丢消息，见模块 docstring 约束 1
        summary = ""
    if not summary:
        return CompactionResult(
            window=window,
            compacted=False,
            tokens_before=before,
            tokens_after=before,
            reason="summarizer_failed",
        )

    compacted = ConversationWindow(
        messages=window.messages[len(to_compress) :], narrative_summary=summary
    )
    return CompactionResult(
        window=compacted,
        compacted=True,
        tokens_before=before,
        tokens_after=count_window_tokens(compacted, counter),
    )
