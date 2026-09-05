"""动作层的领域异常。不含 HTTP 概念——状态码由 API 层映射（CLAUDE.md §7 分层边界）。"""

from __future__ import annotations


class ActionError(Exception):
    """动作层异常基类。"""


class ActionNotFoundError(ActionError):
    """动作不存在，**或者**存在但不属于当前用户（FR-505、FR-804）。

    两种情况共用同一个异常、同一句消息：调用方无从区分，攻击者也就无法通过枚举
    action_id 探测别人有没有发起过退款。API 层统一映射成 404。
    """


class ActionExpiredError(ActionError):
    """动作已过 `expires_at`（FR-504）。API 层映射成 410。"""


class ActionStateError(ActionError):
    """当前状态不接受该操作（如对已执行的动作再次点确认）。API 层映射成 409。"""
