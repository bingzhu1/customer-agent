"""动作状态机（PRD §5.3–5.4、FR-502/504/508）。纯函数，无 IO。

```
proposed ──require_confirmation──▶ awaiting_confirmation ──confirm──▶ executing
   │                                                                     │
   └──require_human────────────▶ awaiting_human ──approve──────────────▶ │
                                                                         ├──succeed──▶ succeeded
                                                                         └──fail─────▶ failed
                                                                                        │
                                                       executing ◀──────retry───────────┘

reject：proposed / awaiting_confirmation / awaiting_human ──▶ rejected
expire：proposed / awaiting_confirmation / awaiting_human ──▶ expired（FR-504）
edit  ：awaiting_human ──▶ awaiting_human（自环，参数变了要重算幂等键）
```

三个终态：`succeeded` / `rejected` / `expired`。`failed` 不是终态——FR-508 要求
执行失败可以重试，重试命中同一幂等键，不会产生第二次副作用。

`awaiting_human` 上的 `edit` 是自环：人工修改参数后状态不变，但**幂等键必须重算**
（FR-605），由 `ActionService` 负责，不在本模块。
"""

from __future__ import annotations

from enum import StrEnum


class ActionStatus(StrEnum):
    """`agent_actions.status` 的取值。"""

    PROPOSED = "proposed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_HUMAN = "awaiting_human"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    REJECTED = "rejected"


class ActionEvent(StrEnum):
    """驱动状态迁移的事件。事件由确定性代码触发，LLM 不能直接发事件。"""

    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_HUMAN = "require_human"
    CONFIRM = "confirm"
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    EXPIRE = "expire"
    SUCCEED = "succeed"
    FAIL = "fail"
    RETRY = "retry"


#: 终态：不接受任何事件。
TERMINAL: frozenset[ActionStatus] = frozenset(
    {ActionStatus.SUCCEEDED, ActionStatus.REJECTED, ActionStatus.EXPIRED}
)

#: 等待用户或人工处理的状态，`expires_at` 只对它们有意义（FR-504）。
WAITING: frozenset[ActionStatus] = frozenset(
    {ActionStatus.AWAITING_CONFIRMATION, ActionStatus.AWAITING_HUMAN}
)

#: 可以进入执行的状态。`failed` 在内是因为 FR-508 允许重试。
EXECUTABLE: frozenset[ActionStatus] = frozenset(
    {ActionStatus.AWAITING_CONFIRMATION, ActionStatus.AWAITING_HUMAN, ActionStatus.FAILED}
)

TRANSITIONS: dict[tuple[ActionStatus, ActionEvent], ActionStatus] = {
    (ActionStatus.PROPOSED, ActionEvent.REQUIRE_CONFIRMATION): ActionStatus.AWAITING_CONFIRMATION,
    (ActionStatus.PROPOSED, ActionEvent.REQUIRE_HUMAN): ActionStatus.AWAITING_HUMAN,
    (ActionStatus.PROPOSED, ActionEvent.REJECT): ActionStatus.REJECTED,
    (ActionStatus.PROPOSED, ActionEvent.EXPIRE): ActionStatus.EXPIRED,
    (ActionStatus.AWAITING_CONFIRMATION, ActionEvent.CONFIRM): ActionStatus.EXECUTING,
    (ActionStatus.AWAITING_CONFIRMATION, ActionEvent.REJECT): ActionStatus.REJECTED,
    (ActionStatus.AWAITING_CONFIRMATION, ActionEvent.EXPIRE): ActionStatus.EXPIRED,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.APPROVE): ActionStatus.EXECUTING,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.EDIT): ActionStatus.AWAITING_HUMAN,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.REJECT): ActionStatus.REJECTED,
    (ActionStatus.AWAITING_HUMAN, ActionEvent.EXPIRE): ActionStatus.EXPIRED,
    (ActionStatus.EXECUTING, ActionEvent.SUCCEED): ActionStatus.SUCCEEDED,
    (ActionStatus.EXECUTING, ActionEvent.FAIL): ActionStatus.FAILED,
    (ActionStatus.FAILED, ActionEvent.RETRY): ActionStatus.EXECUTING,
}


class InvalidTransitionError(ValueError):
    """非法迁移。抛出而不是静默忽略：状态机走错了说明上游逻辑有问题。"""

    def __init__(self, status: ActionStatus, event: ActionEvent) -> None:
        self.status = status
        self.event = event
        super().__init__(f"非法迁移：{status} 上不接受事件 {event}")


def transition(status: ActionStatus, event: ActionEvent) -> ActionStatus:
    """返回迁移后的状态；不允许的组合抛 `InvalidTransitionError`。纯函数。"""
    try:
        return TRANSITIONS[(status, event)]
    except KeyError:
        raise InvalidTransitionError(status, event) from None


def can_transition(status: ActionStatus, event: ActionEvent) -> bool:
    return (status, event) in TRANSITIONS
