"""服务端拥有的身份上下文（ADR-0008、FR-802）。

身份链路固定为：JWT → middleware → `AuthContext` → 依赖注入到 Service / Repository
→ Repository 自动附加 `WHERE user_id = ctx.user_id`。

两条硬约束：

1. 请求体、LLM 输出、工具参数中出现的任何身份字段一律**忽略**，不报错也不采信；
   `AuthContext` 只能由认证中间件构造。
2. 工具签名中不得出现 `user_id` / `tenant_id`——越权在语法层面就不可表达。

本阶段单租户，故只有 `user_id` 与 `roles`；`tenant_id` 待多租户时再加。
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """PRD §3.1 的三个角色。"""

    CUSTOMER = "customer"
    AGENT_OPERATOR = "agent_operator"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class AuthContext:
    """一次请求的调用方身份。不可变，避免中途被业务代码改写。"""

    user_id: int
    roles: frozenset[Role] = field(default_factory=frozenset)

    @classmethod
    def of(cls, user_id: int, roles: Iterable[Role | str] = (Role.CUSTOMER,)) -> "AuthContext":
        """从认证结果构造。未知角色字符串直接抛错，不静默忽略。"""
        return cls(user_id=user_id, roles=frozenset(Role(r) for r in roles))

    def has_role(self, role: Role) -> bool:
        return role in self.roles
