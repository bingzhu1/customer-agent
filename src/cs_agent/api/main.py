"""应用装配。**这里只做装配，不写任何业务逻辑**（CLAUDE.md §7）。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware

from cs_agent.api.errors import register_exception_handlers
from cs_agent.api.middleware import AuthenticationMiddleware, RequestContextMiddleware
from cs_agent.api.routes import ops, v1
from cs_agent.db.base import get_engine
from cs_agent.observability.logging import configure_logging, get_logger
from cs_agent.services.chat import close_extraction_queue
from cs_agent.settings import get_settings

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("app_starting", app_env=settings.app_env)
    if len(settings.jwt_secret) < 32:
        # HS256 的密钥短于 32 字节等于自降强度（RFC 7518 §3.2）
        logger.warning("jwt_secret_too_short", length=len(settings.jwt_secret))
    yield
    # 优雅关闭的完整版（SIGTERM → 停止接新请求）是 FR-107，Phase 6 落地
    close_extraction_queue()
    get_engine().dispose()
    logger.info("app_stopped")


def create_app() -> FastAPI:
    """工厂函数：测试可以按需造多个实例，配置不通过全局变量传。"""
    configure_logging(get_settings().log_level)
    app = FastAPI(
        title="cs-agent",
        version="0.1.0",
        lifespan=lifespan,
        # 列表顺序即由外到内：CORS 必须在最外层（预检请求不带 token，不能被认证拦掉），
        # 之后 request_id 包住认证，401 也要有 request_id 与指标
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=get_settings().cors_origin_list,
                allow_credentials=True,
                allow_methods=["GET", "POST", "OPTIONS"],
                # 前端要带 Authorization；X-Request-ID 让它能把请求和后端日志对上
                allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
                expose_headers=["X-Request-ID"],
            ),
            Middleware(RequestContextMiddleware),
            Middleware(AuthenticationMiddleware),
        ],
    )
    register_exception_handlers(app)
    app.include_router(ops.router)
    app.include_router(v1.router)
    return app


app = create_app()
