"""`ActionProposal` 与幂等键（PRD §5.3、§7.4，FR-501/503/605）。

**LLM 只能产出 `ActionProposal`**（红线 2、ADR-0005）：提议不是执行，它没有副作用，
要经过策略引擎判定 + 用户确认或人工审批，才会被 `ActionService` 落库并执行。

幂等键是防重复退款的**唯一**手段的输入端：
`sha256(user_id | action_type | 规范化 params | window_start)`，写进
`agent_actions.idempotency_key`，由数据库唯一索引兜住并发与 checkpoint 重放（ADR-0003）。

两条性质由测试固定：

- **同参数同窗口必同键**——网络重试、用户连点、图重放都会算出同一个键；
- **任一输入变化必不同键**——人工审批改了金额（FR-605）就是另一笔动作，
  不能命中旧键把原来的退款结果当成"已执行"返回。

`params` 规范化规则：键排序、UTF-8 原样、无多余空白；`Decimal` 先 `normalize()` 再定点格式化，
所以 `89`、`89.0`、`89.00` 是同一个金额、同一个键，而 `89.5` 与 `89` 不是。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

#: `params` 允许的取值类型。JSONB 列存得下，且都能确定性地序列化。
ParamValue = str | int | float | bool | Decimal | None


class ActionType(StrEnum):
    """本阶段只有两种写操作。新增类型必须同步 `REQUIRED_PARAMS`。"""

    REFUND = "refund"
    CREATE_TICKET = "create_ticket"


#: 每种动作的必填参数。缺一个就构造不出 proposal——让"参数不全的写操作"不可表达。
REQUIRED_PARAMS: dict[ActionType, frozenset[str]] = {
    ActionType.REFUND: frozenset({"order_id", "amount", "reason"}),
    ActionType.CREATE_TICKET: frozenset({"order_id", "reason"}),
}


class InvalidProposalError(ValueError):
    """提议的参数不满足该动作类型的必填约束。"""


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """一次写操作的提议。冻结不可变：提议一旦产生就不该被下游节点改写。

    `params` 是结构化的业务参数（`order_id` / `amount` / `reason`），
    **不含身份**——`user_id` 由 `AuthContext` 注入，不进提议（红线 1、ADR-0008）。
    """

    action_type: ActionType
    params: Mapping[str, ParamValue]

    def __post_init__(self) -> None:
        required = REQUIRED_PARAMS[self.action_type]
        missing = sorted(required - set(self.params))
        if missing:
            raise InvalidProposalError(f"{self.action_type}: 缺少必填参数 {missing}")
        for forbidden in ("user_id", "tenant_id"):
            if forbidden in self.params:
                raise InvalidProposalError(
                    f"{self.action_type}: params 不得包含身份字段 {forbidden!r}"
                )

    def idempotency_key(self, user_id: int, window_start: datetime) -> str:
        """便捷入口，等价于 `idempotency_key(user_id, self.action_type, self.params, ...)`。"""
        return idempotency_key(user_id, self.action_type, self.params, window_start)

    def as_jsonb(self) -> dict[str, Any]:
        """写进 `agent_actions.params`（JSONB）的形态：`Decimal` 转字符串，避免浮点失真。"""
        return {k: _jsonable(v) for k, v in self.params.items()}


def canonical_params(params: Mapping[str, ParamValue]) -> str:
    """params 的规范化 JSON。键排序、紧凑分隔符、中文不转义。"""
    return json.dumps(
        {k: _jsonable(v) for k, v in params.items()},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def idempotency_key(
    user_id: int,
    action_type: ActionType | str,
    params: Mapping[str, ParamValue],
    window_start: datetime,
) -> str:
    """`sha256(user_id|action_type|params|window_start)` 的十六进制摘要（64 字符）。

    `window_start` 一律换算成 UTC 再参与摘要，避免同一时刻因时区写法不同算出两个键；
    naive 时间按 UTC 解释（调用方应当传 aware 时间）。
    """
    window = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
    material = "|".join(
        (
            str(user_id),
            str(action_type),
            canonical_params(params),
            window.astimezone(UTC).isoformat(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _jsonable(value: ParamValue) -> str | int | float | bool | None:
    """`Decimal` → 定点字符串；其余原样。`89`、`89.0`、`89.00` 都得到 "89"。"""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value
