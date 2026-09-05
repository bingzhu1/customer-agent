"""structlog 配置（PRD §14.1）。

`request_id` / `thread_id` / `user_id` 通过 contextvars 绑定，
一次绑定后本请求内所有日志自动带上，不必层层传参。

脱敏：密钥、token、完整邮箱、支付信息一律不入日志。本模块只保证
"不主动记录"——`Settings` 里的敏感字段已用 `repr=False` 挡住，
调用方不得自己把 token 拼进日志消息。
"""

import logging
import sys

import structlog

_configured = False


def configure_logging(level: str = "INFO") -> None:
    """进程级配置，重复调用无副作用（测试里会被多次触发）。"""
    global _configured
    if _configured:
        return

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def bind_request_context(**fields: str | int | None) -> None:
    """绑定本请求的公共字段；None 值不绑定，避免日志里出现一堆 null。"""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in fields.items() if v is not None})


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
