"""HTTP 中间件：request_id → 指标 → 认证。

顺序（由外到内）很重要：

1. `RequestContextMiddleware` 最外层——401 也要带 `request_id`、也要计入指标；
2. `AuthenticationMiddleware` 在内层——只保护业务路径，`/health` 等不需要 token。

中间件里**不能 raise `ApiError`**：异常处理器挂在更内层的 ExceptionMiddleware 上，
中间件抛出的异常会直接变成 500。所以这里一律 `return error_response(...)`。
"""

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cs_agent.api.errors import UnauthenticatedError, error_response
from cs_agent.auth.jwt import AuthError, decode_token, parse_bearer
from cs_agent.observability import metrics
from cs_agent.observability.logging import (
    bind_request_context,
    clear_request_context,
    get_logger,
)

logger = get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]

# 无需认证的路径（PRD §8.1：health / ready / metrics 无鉴权，metrics 靠内网隔离）
PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        # dev-only 的签发接口：不放行就拿不到第一个 token。
        # 该路由只在 APP_ENV=dev 时注册，生产里它根本不存在（见 routes/v1.py）。
        "/v1/dev/token",
    }
)

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LEN = 64


class RequestContextMiddleware(BaseHTTPMiddleware):
    """生成 request_id、绑定日志上下文、记录耗时指标。"""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = _incoming_request_id(request) or f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id
        bind_request_context(request_id=request_id)
        started = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            # endpoint 用路由模板而非真实路径，否则 /v1/threads/{id} 会撑爆标签基数
            endpoint = _endpoint_label(request)
            metrics.request_duration_seconds.labels(endpoint=endpoint, status=status).observe(
                time.perf_counter() - started
            )
            # BaseHTTPMiddleware 的 call_next 在独立 task 里跑，认证中间件绑的 contextvars
            # 传不回外层，所以这里显式把 user_id 取出来补进访问日志（PRD §14.1 要求必带）
            auth = getattr(request.state, "auth", None)
            logger.info(
                "http_request",
                method=request.method,
                endpoint=endpoint,
                status=status,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                request_id=request_id,
                user_id=getattr(auth, "user_id", None),
            )
            clear_request_context()


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """JWT → `AuthContext`，挂到 `request.state.auth`（FR-801 / FR-802）。

    请求体、查询参数里的任何身份字段都不看：身份**只**来自这里。
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        if request.method == "OPTIONS":
            # CORS 预检不带 Authorization，拦掉它等于关掉跨域
            return await call_next(request)

        try:
            token = parse_bearer(request.headers.get("Authorization"))
            auth = decode_token(token)
        except AuthError as exc:
            # 失败原因只进日志，响应里统一是 UNAUTHENTICATED，不告诉调用方是签名错还是过期
            logger.warning("auth_failed", reason=str(exc))
            return error_response(request, UnauthenticatedError())

        request.state.auth = auth
        bind_request_context(user_id=auth.user_id)
        return await call_next(request)


def _incoming_request_id(request: Request) -> str | None:
    """允许上游透传 request_id 以便跨服务串联；做长度与字符净化，防日志注入。"""
    raw = request.headers.get(REQUEST_ID_HEADER)
    if not raw:
        return None
    cleaned = "".join(c for c in raw if c.isalnum() or c in "-_")[:_MAX_REQUEST_ID_LEN]
    return cleaned or None


def _endpoint_label(request: Request) -> str:
    route = request.scope.get("route")
    path: str | None = getattr(route, "path", None)
    return path or "unmatched"
