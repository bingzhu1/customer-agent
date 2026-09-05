"""JWT 签发与校验（FR-801）。

本阶段没有登录流程：token 由 `issue_token` 离线签发（`make token` 与测试用），
服务端只做校验。校验失败一律抛 `AuthError`，由 API 层翻成 401 `UNAUTHENTICATED`，
**不区分**"签名错""过期""缺字段"——错误细节只进日志，不回给调用方。

固定 HS256 并显式传 `algorithms=["HS256"]`：防 alg 混淆攻击
（攻击者把 header 改成 `none` 或 `RS256`，让校验方拿公钥当 HMAC 密钥用）。
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from cs_agent.auth.context import AuthContext, Role
from cs_agent.settings import get_settings

ALGORITHM = "HS256"


class AuthError(Exception):
    """token 缺失 / 无效 / 过期。对外统一为 401，不泄露具体原因。"""


def issue_token(
    user_id: int,
    roles: Iterable[Role | str] = (Role.CUSTOMER,),
    *,
    expires_in: timedelta | None = None,
    now: datetime | None = None,
) -> str:
    """签发 token。`now` 可注入，便于测试构造过期 token。"""
    settings = get_settings()
    issued_at = now or datetime.now(UTC)
    ttl = expires_in or timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "roles": [Role(r).value for r in roles],
        "iss": settings.jwt_issuer,
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> AuthContext:
    """校验 token 并构造 `AuthContext`。身份只从这里来，绝不从请求体来（FR-802）。"""
    settings = get_settings()
    if not settings.jwt_secret:
        # 没配密钥就放行等于没有认证，宁可全部 401
        raise AuthError("JWT_SECRET 未配置")
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc

    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise AuthError("sub 不是合法的 user_id") from exc

    roles = claims.get("roles") or []
    if not isinstance(roles, list):
        raise AuthError("roles 必须是数组")
    try:
        return AuthContext.of(user_id, roles)
    except ValueError as exc:  # 未知角色一律拒绝，不静默降级为无角色
        raise AuthError(str(exc)) from exc


def parse_bearer(header_value: str | None) -> str:
    """从 `Authorization: Bearer <token>` 取出 token。"""
    if not header_value:
        raise AuthError("缺少 Authorization 头")
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Authorization 头格式不是 Bearer")
    return token.strip()
