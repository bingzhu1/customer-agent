"""统一错误信封（PRD §8.4）。

所有失败响应都是同一个形状，调用方不必猜：

```json
{"error": {"code": "NOT_FOUND", "message": "...", "retryable": false},
 "request_id": "req_..."}
```

两条设计意图：

- **404 不区分"不存在"与"不属于你"**：与 Repository 层的 `None` 语义一致（FR-804），
  避免通过枚举 id 探测存在性。
- **500 不回原始异常**：细节只进日志，响应里只有固定文案。
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from cs_agent.observability import metrics
from cs_agent.observability.logging import get_logger

logger = get_logger(__name__)


class ApiError(Exception):
    """业务侧主动抛出的错误。`code` 必须是 §8.4 表里的值。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.headers = headers or {}
        # 仅参数校验错误会填：字段位置与错误类型，不含收到的原值
        self.details: list[dict[str, Any]] | None = None


class UnauthenticatedError(ApiError):
    def __init__(self, message: str = "认证失败：token 缺失、无效或已过期") -> None:
        super().__init__(401, "UNAUTHENTICATED", message)


class ForbiddenError(ApiError):
    def __init__(self, message: str = "角色权限不足") -> None:
        super().__init__(403, "FORBIDDEN", message)


class NotFoundError(ApiError):
    """资源不存在**或**不属于当前用户——两者对外不可区分。"""

    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(404, "NOT_FOUND", message)


class DependencyUnavailableError(ApiError):
    def __init__(self, message: str = "依赖不可用") -> None:
        super().__init__(503, "DEPENDENCY_UNAVAILABLE", message, retryable=True)


def error_response(request: Request, error: ApiError) -> JSONResponse:
    metrics.error_total.labels(error_code=error.code).inc()
    body: dict[str, Any] = {
        "error": {"code": error.code, "message": error.message, "retryable": error.retryable},
        "request_id": getattr(request.state, "request_id", None),
    }
    if error.details is not None:
        body["error"]["details"] = error.details
    return JSONResponse(status_code=error.status_code, content=body, headers=error.headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        error = ApiError(400, "INVALID_REQUEST", "参数校验失败")
        error.details = _safe_details(exc)
        return error_response(request, error)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODE_MAP.get(exc.status_code, "INTERNAL_ERROR")
        detail = exc.detail if isinstance(exc.detail, str) else code
        return error_response(request, ApiError(exc.status_code, code, detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", error_class=exc.__class__.__name__)
        return error_response(request, ApiError(500, "INTERNAL_ERROR", "服务内部错误"))


_HTTP_CODE_MAP = {
    400: "INVALID_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "INVALID_REQUEST",
    409: "ACTION_STATE_CONFLICT",
    410: "ACTION_EXPIRED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "DEPENDENCY_UNAVAILABLE",
    504: "LLM_TIMEOUT",
}


def _safe_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """只回字段位置与错误类型，不回收到的原值（可能含敏感内容）。"""
    return [{"loc": list(e.get("loc", [])), "type": e.get("type", "")} for e in exc.errors()]
