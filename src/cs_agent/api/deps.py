"""FastAPI 依赖：数据库会话、身份、Repository。

身份不从路由参数取，也不从请求体取——只从中间件放进 `request.state.auth` 的
`AuthContext` 取（FR-802、ADR-0008）。路由函数想拿到别人的身份，语法上做不到。
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from cs_agent.api.errors import ForbiddenError, UnauthenticatedError
from cs_agent.auth.context import AuthContext, Role
from cs_agent.db.base import get_session_factory
from cs_agent.repositories.biz import BizRepository


def get_session() -> Iterator[Session]:
    """一次请求一个会话，请求结束即关闭。"""
    with get_session_factory()() as session:
        yield session


def get_auth_context(request: Request) -> AuthContext:
    """取中间件校验过的身份。中间件已挡住无 token 的请求，这里是兜底。"""
    auth: AuthContext | None = getattr(request.state, "auth", None)
    if auth is None:
        raise UnauthenticatedError()
    return auth


AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
SessionDep = Annotated[Session, Depends(get_session)]


def require_role(role: Role) -> object:
    """角色门槛（PRD §3.1）。用法：`dependencies=[Depends(require_role(Role.AGENT_OPERATOR))]`。"""

    def _check(auth: AuthDep) -> AuthContext:
        if not auth.has_role(role):
            raise ForbiddenError()
        return auth

    return Depends(_check)


def get_biz_repository(session: SessionDep, auth: AuthDep) -> BizRepository:
    """Repository 在构造时就绑定身份，之后每条查询自动带 scope。"""
    return BizRepository(session, auth)


BizRepoDep = Annotated[BizRepository, Depends(get_biz_repository)]
