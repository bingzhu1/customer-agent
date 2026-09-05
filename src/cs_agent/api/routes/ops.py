"""运维接口：`/health`、`/ready`、`/metrics`（FR-105 / FR-106）。无鉴权。"""

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from cs_agent.api.errors import DependencyUnavailableError
from cs_agent.db.base import get_engine
from cs_agent.observability import metrics

router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> dict[str, str]:
    """存活检查：**不依赖任何外部组件**，进程活着就返回 200。

    依赖挂了不该让编排系统重启进程——那是 `/ready` 的事。
    """
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, Any]:
    """就绪检查：探 DB；向量索引 Phase 2 接入后一并探。"""
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    # pgvector 索引 Phase 2 才建，此刻没有可探的东西，如实标注而不是假装 ok
    checks["vector_index"] = "not_applicable"

    if any(v == "unavailable" for v in checks.values()):
        raise DependencyUnavailableError("依赖未就绪")
    return {"status": "ready", "checks": checks}


@router.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus 文本格式（PRD §14.2）。生产环境靠内网 / 网关限制访问。"""
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)
