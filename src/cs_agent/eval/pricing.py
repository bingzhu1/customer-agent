"""模型定价（每 1M token，美元），用于 estimated cost / session（PRD §12.4、§13.4）。

缓存读按输入价 0.1 倍、缓存写按 1.25 倍计。价格变动只改这里。
"""

from __future__ import annotations

from dataclasses import dataclass

from cs_agent.eval.protocol import Usage


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float


PRICES: dict[str, Price] = {
    "claude-sonnet-5": Price(2.00, 10.00),
    "claude-haiku-4-5": Price(1.00, 5.00),
    "claude-opus-5": Price(5.00, 25.00),
}

CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25


def estimate_cost_usd(usage: Usage, default_model: str) -> float:
    """Usage 不按模型拆分 token，故按本轮用到的最贵模型计价（保守估计）。"""
    candidates = [PRICES[m] for m in usage.models if m in PRICES] or [
        PRICES.get(default_model, PRICES["claude-sonnet-5"])
    ]
    price = max(candidates, key=lambda p: p.input_per_m)
    return (
        usage.input_tokens * price.input_per_m
        + usage.cache_read_input_tokens * price.input_per_m * CACHE_READ_FACTOR
        + usage.cache_creation_input_tokens * price.input_per_m * CACHE_WRITE_FACTOR
        + usage.output_tokens * price.output_per_m
    ) / 1_000_000
