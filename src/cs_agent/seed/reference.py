"""Phase 0 夹具的公共时间基准（docs/phase0-fixtures.md §0）。

seed、策略引擎与 eval runner 都以 `EVAL_NOW` 为"今天"，保证用例不随真实日期漂移。
"""

from datetime import UTC, datetime, timedelta

EVAL_NOW: datetime = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
"""评估参考时刻。seed 中所有日期都是相对该时刻推算后写死的绝对时间。"""

SEED_RANDOM_SEED: int = 20260901
"""seed 生成器的固定随机种子，保证填充数据可复现。"""


def days_ago(n: int, *, hours: int = 0) -> datetime:
    """`EVAL_NOW - n 天`（可再减 hours 小时），用于把契约里的"N 天前"换成绝对时间。"""
    return EVAL_NOW - timedelta(days=n, hours=hours)


def days_after(n: int) -> datetime:
    """`EVAL_NOW + n 天`，用于在途订单的预计送达时间。"""
    return EVAL_NOW + timedelta(days=n)


def days_since(ts: datetime) -> int:
    """契约定义：`days_since_delivery = (EVAL_NOW - delivered_at).days`。"""
    return (EVAL_NOW - ts).days
