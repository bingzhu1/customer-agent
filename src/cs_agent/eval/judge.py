"""LLM 评判：只做两项——语气是否得当、回答是否可由引用的策略文本支撑（PRD §12.1 第 2 条）。

结果单列、不进硬门槛；需人工抽检校准。默认关闭，CLI 用 --judge 开启。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

import anthropic

from cs_agent.eval.protocol import TurnResult
from cs_agent.eval.schema import GoldenCase
from cs_agent.policy.schema import PolicySet
from cs_agent.settings import get_settings


@dataclass(frozen=True)
class JudgeResult:
    tone_appropriate: bool
    groundedness: bool
    rationale: str = ""
    error: str | None = None


class Judge(ABC):
    @abstractmethod
    def judge(self, case: GoldenCase, final: TurnResult) -> JudgeResult: ...


_SYSTEM = (
    "你是客服质检员。给定客户最后一句话、客服回复、以及回复引用的政策原文，"
    "只回答两个是非判断：\n"
    "1. tone_appropriate：语气是否礼貌、专业、不推卸、不过度承诺；\n"
    "2. groundedness：回复中关于政策/规则的每个事实性陈述，是否都能由给出的政策原文支撑。"
    "若回复没有做任何政策性陈述，groundedness 记为 true；"
    "若做了陈述但没有任何政策原文，记为 false。\n"
    "用 rationale 写一句中文理由。不要评价业务决策对错。"
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "tone_appropriate": {"type": "boolean"},
        "groundedness": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
    "required": ["tone_appropriate", "groundedness", "rationale"],
    "additionalProperties": False,
}


class LlmJudge(Judge):
    def __init__(self, policies: PolicySet, client: anthropic.Anthropic | None = None) -> None:
        settings = get_settings()
        self._client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.llm_model_judge
        self._policies = policies

    def _cited_text(self, final: TurnResult) -> str:
        parts: list[str] = []
        for c in final.citations:
            try:
                rule = self._policies.by_id(c.policy_id)
            except KeyError:
                parts.append(f"[{c.policy_id}] （未知策略 id）")
                continue
            parts.append(f"[{rule.id} v{rule.version}] {rule.title}\n{rule.human_text.strip()}")
        return "\n\n".join(parts) if parts else "（无引用）"

    def judge(self, case: GoldenCase, final: TurnResult) -> JudgeResult:
        last_user = next((t.user for t in reversed(case.turns) if t.user), "（确认操作）")
        prompt = (
            f"客户最后一句：{last_user}\n\n客服回复：{final.reply}\n\n"
            f"引用的政策原文：\n{self._cited_text(final)}"
        )
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=512,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            )
            text = next(b.text for b in resp.content if b.type == "text")
            data = json.loads(text)
            return JudgeResult(
                tone_appropriate=bool(data["tone_appropriate"]),
                groundedness=bool(data["groundedness"]),
                rationale=str(data.get("rationale", "")),
            )
        except (anthropic.APIError, StopIteration, KeyError, ValueError) as e:
            return JudgeResult(tone_appropriate=False, groundedness=False, error=str(e))
