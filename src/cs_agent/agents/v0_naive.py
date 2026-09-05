"""V0 naive baseline：裸 LLM，无工具、无检索、无策略、无记忆结构。

这是 PRD §12.6 V0 行的对照组，**故意做得很朴素**：把多轮对话原样送给模型，
拿回一段文本就算数。它的作用是量化"什么都不做"的水位，让后续每一版的增量可归因。

因此以下都是有意为之，不是没写完：

- `decision` 恒为 ANSWER、`reason_code` 恒为 OK——裸 LLM 没有决策层，不会拒绝、不会升级、
  不会要求确认。SEC / ESC / IDEM 类用例几乎必然失败，这正是要测出来的东西。
- `citations` 与 `tool_calls` 恒为空——没有检索也没有工具，报不出引用就不报，绝不编造。
- 身份**不进入 prompt**。`Auth` 只用于开会话，不拼进任何送给模型的文本（CLAUDE.md 红线 1）。
  V0 因此无法做归属校验，SEC 类越权用例会暴露这个缺口——这是基线该有的样子。
- 不做 prompt caching。缓存是 Phase 6 的成本手段，放进 V0 会污染"低成本基线"的口径。

只有 usage 是认真记的：它是 §12.6 Tokens / Cost 两列的数据来源。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Protocol, cast

from cs_agent.domain.enums import DecisionOutcome, ReasonCode
from cs_agent.eval.protocol import AgentSession, AgentUnderTest, TurnResult, Usage
from cs_agent.eval.schema import Auth, ToolFault
from cs_agent.settings import get_settings

SYSTEM_PROMPT = "你是一家电商公司的在线客服助理。请用中文、礼貌、简洁地回答用户的问题。"
"""朴素到极点的 system prompt：没有政策、没有边界、没有工具说明。故意如此。"""

CONFIRM_TEXT = "确认。"
"""`confirm()` 送给模型的固定文本。V0 没有 pending_action 概念，只能把它当普通一轮。"""

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 2  # PRD §13.2：可重试错误最多退避重试 2 次


class _MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class LlmClient(Protocol):
    """本模块只用到 `client.messages.create(...)`。

    收窄成这个形状是为了让测试能注入替身而不必碰网络——真实 SDK 客户端结构上满足它。
    """

    @property
    def messages(self) -> _MessagesAPI: ...


def _extract_text(message: Any) -> str:
    """拼接回复里的 text block，忽略其他类型（如 thinking / tool_use）。"""
    parts = [
        block.text
        for block in getattr(message, "content", [])
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts).strip()


def _extract_usage(message: Any, model: str) -> Usage:
    raw = getattr(message, "usage", None)

    def field(name: str) -> int:
        return int(getattr(raw, name, 0) or 0)

    return Usage(
        llm_calls=1,
        input_tokens=field("input_tokens"),
        output_tokens=field("output_tokens"),
        cache_read_input_tokens=field("cache_read_input_tokens"),
        cache_creation_input_tokens=field("cache_creation_input_tokens"),
        models=[model],
    )


class V0NaiveSession(AgentSession):
    """一条会话。持有完整对话历史，每轮把历史原样重发（不压缩、不摘要）。

    `_lock` 保证并发 `confirm()` 下历史不会交错——protocol 要求实现能承受并发确认。
    V0 没有幂等机制，两次确认会真的调用两次模型，这个开销会如实计入 usage。
    """

    def __init__(
        self,
        client: LlmClient,
        *,
        model: str,
        system_prompt: str,
        max_tokens: int,
    ) -> None:
        self._client = client
        self._model = model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._messages: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def send_user(self, text: str, *, faults: list[ToolFault] | None = None) -> TurnResult:
        """`faults` 是工具故障注入，V0 没有工具，按 protocol 约定忽略。"""
        return self._turn(text)

    def confirm(self) -> TurnResult:
        """V0 无 pending_action，把确认当作一句普通用户发言送给模型。

        按 protocol 约定返回 ANSWER / OK；不会出现 IDEMPOTENT_REPLAY，
        幂等类用例因此失败，这是基线的真实水位。
        """
        return self._turn(CONFIRM_TEXT)

    def _turn(self, text: str) -> TurnResult:
        with self._lock:
            self._messages.append({"role": "user", "content": text})
            payload = list(self._messages)

            message = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system_prompt,
                messages=payload,
            )

            reply = _extract_text(message)
            self._messages.append({"role": "assistant", "content": reply})

        return TurnResult(
            reply=reply,
            decision=DecisionOutcome.ANSWER,
            reason_code=ReasonCode.OK,
            citations=[],
            tool_calls=[],
            usage=_extract_usage(message, self._model),
            debug={"turns_sent": len(payload)},
        )


class V0NaiveAgent(AgentUnderTest):
    """V0 工厂。`client` 惰性创建，因此没有 API key 也能构造实例（registry 会无参构造）。"""

    name = "v0-naive"

    def __init__(
        self,
        client: LlmClient | None = None,
        *,
        model: str | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._client = client
        self._model = model or get_settings().llm_model_primary
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s

    @property
    def model(self) -> str:
        return self._model

    def _ensure_client(self) -> LlmClient:
        client = self._client
        if client is None:
            from anthropic import Anthropic

            settings = get_settings()
            # cast：真实 SDK 的 messages.create 是重载签名，与 LlmClient 里的 **kwargs 形式
            # 不构成 mypy 意义上的结构匹配，但运行时完全满足本模块的调用方式。
            client = cast(
                LlmClient,
                Anthropic(
                    api_key=settings.anthropic_api_key or None,
                    timeout=self._timeout_s,
                    max_retries=DEFAULT_MAX_RETRIES,
                ),
            )
            self._client = client
        return client

    def start_session(self, auth: Auth, *, now: datetime) -> AgentSession:
        """`auth` 与 `now` 都不进入 prompt。

        身份不经过 LLM 是红线 1；`now` 对 V0 无意义——它没有任何日期计算，
        塞进 prompt 只会让基线偷跑（真实的裸 LLM 不知道"今天"是哪天）。
        """
        return V0NaiveSession(
            self._ensure_client(),
            model=self._model,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
        )


AGENT = V0NaiveAgent
