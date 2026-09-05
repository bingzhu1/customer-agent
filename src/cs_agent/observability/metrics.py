"""Prometheus 指标（PRD §14.2）。

模块级单例：指标只能在默认 registry 注册一次，重复注册会抛异常，
所以定义放模块顶层，`create_app()` 被调用多次（测试）也不会重复注册。

本 milestone 只落地 HTTP 层的两个指标；其余 11 个随对应 Phase 加入
（LLM / 工具 / 检索 / 决策 / 成本等），不提前建空指标。
"""

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.registry import REGISTRY

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

request_duration_seconds = Histogram(
    "agent_request_duration_seconds",
    "HTTP 请求耗时",
    labelnames=("endpoint", "status"),
)

error_total = Counter(
    "agent_error_total",
    "按错误码计数的失败请求",
    labelnames=("error_code",),
)


def render(registry: CollectorRegistry = REGISTRY) -> bytes:
    """渲染 Prometheus 文本格式（FR-106）。"""
    return generate_latest(registry)
