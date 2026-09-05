"""运维接口：/health 不依赖外部、/ready 探依赖、/metrics 返回 Prometheus 文本（FR-105/106）。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cs_agent.api.main import create_app
from cs_agent.settings import get_settings


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_health_needs_no_token_and_no_database(client: TestClient) -> None:
    """存活检查必须不碰外部依赖：不带 token 也能 200。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_carries_request_id(client: TestClient) -> None:
    assert client.get("/health").headers["X-Request-ID"].startswith("req_")


def test_request_id_is_propagated_from_caller(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "req_upstream_1"})
    assert resp.headers["X-Request-ID"] == "req_upstream_1"


def test_request_id_is_sanitized(client: TestClient) -> None:
    """上游传来的 id 要净化，避免换行等字符污染日志。"""
    resp = client.get("/health", headers={"X-Request-ID": "bad id\twith spaces"})
    assert resp.headers["X-Request-ID"] == "badidwithspaces"


def test_ready_reports_dependencies(client: TestClient) -> None:
    resp = client.get("/ready")
    body = resp.json()
    if resp.status_code == 503:  # pragma: no cover - 取决于本机数据库是否起着
        assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert body["error"]["retryable"] is True
        pytest.skip("数据库不可达，只校验了 /ready 的失败分支")
    assert resp.status_code == 200
    assert body["checks"]["database"] == "ok"
    # 向量索引 Phase 2 才有，这里如实标注而不是假装 ok
    assert body["checks"]["vector_index"] == "not_applicable"


def test_metrics_exposes_prometheus_text(client: TestClient) -> None:
    client.get("/health")  # 先打一个请求，保证直方图里有样本
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "agent_request_duration_seconds" in resp.text


def test_metrics_labels_use_route_template(client: TestClient) -> None:
    """标签用路由模板而不是真实路径，否则带 id 的路径会撑爆标签基数。"""
    client.get("/health")
    assert 'endpoint="/health"' in client.get("/metrics").text


def test_warmup_is_disabled_in_tests() -> None:
    """测试环境不预热：否则每次起 app 都会真的调一次模型（conftest 里钉死）。"""
    assert get_settings().warmup_on_startup is False
    assert get_settings().llm_configured is False


def test_warmup_survives_unavailable_dependencies() -> None:
    """预热失败只记日志，不能让服务起不来——余额耗尽、DB 没起都属于这种情况。"""
    from cs_agent.api.main import _warmup

    broken = get_settings().model_copy(
        update={"database_url": "postgresql+psycopg://nobody@127.0.0.1:1/none"}
    )
    _warmup(broken)  # 不抛异常即通过
