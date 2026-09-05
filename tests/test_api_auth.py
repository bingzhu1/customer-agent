"""JWT 认证与身份来源（FR-801 / FR-802 / FR-108、ADR-0008）。

核心断言：身份**只**来自 token。请求体、查询参数、路径参数里写什么身份都不算数。
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog.testing
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from cs_agent.api.deps import AuthDep
from cs_agent.api.main import create_app
from cs_agent.api.middleware import PUBLIC_PATHS
from cs_agent.auth.context import Role
from cs_agent.auth.jwt import issue_token
from cs_agent.settings import get_settings

MAIN_USER = 101
OTHER_USER = 202


class _Probe(BaseModel):
    """故意带上身份字段，用来验证它们被忽略（FR-802）。"""

    user_id: int | None = None
    tenant_id: str | None = None
    text: str = ""


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """在真实 app 上挂一个探针路由：不把这种回显接口做进产品代码。"""
    application = create_app()

    @application.post("/v1/_probe")
    def _probe(payload: _Probe, auth: AuthDep) -> dict[str, Any]:
        return {"user_id": auth.user_id, "echo": payload.model_dump()}

    return application


@pytest.fixture(scope="module")
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- FR-801：无效 / 过期 token 返回 401 ----


def test_missing_token_returns_401(client: TestClient) -> None:
    resp = client.get("/v1/whoami")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


def test_401_response_still_has_request_id(client: TestClient) -> None:
    """request_id 中间件在认证外层，认证失败也要能追踪。"""
    resp = client.get("/v1/whoami")
    assert resp.headers["X-Request-ID"].startswith("req_")
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic abc", "token abc", "Bearer not-a-jwt", "Bearer a.b.c"],
)
def test_malformed_authorization_header_returns_401(client: TestClient, header: str) -> None:
    resp = client.get("/v1/whoami", headers={"Authorization": header})
    assert resp.status_code == 401


def test_expired_token_returns_401(client: TestClient) -> None:
    expired = issue_token(
        MAIN_USER,
        now=datetime.now(UTC) - timedelta(hours=2),
        expires_in=timedelta(minutes=1),
    )
    assert client.get("/v1/whoami", headers=_bearer(expired)).status_code == 401


def test_token_signed_with_wrong_secret_returns_401(client: TestClient) -> None:
    import jwt as pyjwt

    forged = pyjwt.encode(
        {
            "sub": str(MAIN_USER),
            "roles": ["customer"],
            "iss": get_settings().jwt_issuer,
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        "wrong-secret",
        algorithm="HS256",
    )
    assert client.get("/v1/whoami", headers=_bearer(forged)).status_code == 401


def test_alg_none_token_returns_401(client: TestClient) -> None:
    """alg 混淆攻击：header 改成 none 试图跳过签名校验。"""
    import jwt as pyjwt

    unsigned = pyjwt.encode(
        {
            "sub": str(MAIN_USER),
            "roles": ["customer"],
            "iss": get_settings().jwt_issuer,
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        key="",
        algorithm="none",
    )
    assert client.get("/v1/whoami", headers=_bearer(unsigned)).status_code == 401


def test_token_from_other_issuer_returns_401(client: TestClient) -> None:
    import jwt as pyjwt

    foreign = pyjwt.encode(
        {
            "sub": str(MAIN_USER),
            "roles": ["customer"],
            "iss": "someone-else",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert client.get("/v1/whoami", headers=_bearer(foreign)).status_code == 401


def test_unknown_role_is_rejected_not_downgraded(client: TestClient) -> None:
    """未知角色必须 401，不能静默变成"没有角色"后继续放行。"""
    import jwt as pyjwt

    token = pyjwt.encode(
        {
            "sub": str(MAIN_USER),
            "roles": ["superuser"],
            "iss": get_settings().jwt_issuer,
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert client.get("/v1/whoami", headers=_bearer(token)).status_code == 401


# ---- 正常路径 ----


def test_valid_token_returns_identity_from_token(client: TestClient) -> None:
    resp = client.get("/v1/whoami", headers=_bearer(issue_token(MAIN_USER, [Role.CUSTOMER])))
    assert resp.status_code == 200
    assert resp.json() == {"user_id": MAIN_USER, "roles": ["customer"]}


# ---- FR-802：请求体里的身份字段一律忽略 ----


def test_body_identity_fields_are_ignored(client: TestClient) -> None:
    resp = client.post(
        "/v1/_probe",
        headers=_bearer(issue_token(MAIN_USER)),
        json={"user_id": OTHER_USER, "tenant_id": "evil-tenant", "text": "hi"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 请求体原样回显，但生效的身份仍是 token 里的那个
    assert body["echo"]["user_id"] == OTHER_USER
    assert body["user_id"] == MAIN_USER


def test_query_string_identity_is_ignored(client: TestClient) -> None:
    resp = client.get(
        "/v1/whoami", params={"user_id": OTHER_USER}, headers=_bearer(issue_token(MAIN_USER))
    )
    assert resp.json()["user_id"] == MAIN_USER


# ---- 日志字段（PRD §14.1）----


def test_access_log_carries_request_id_and_user_id(client: TestClient) -> None:
    with structlog.testing.capture_logs() as logs:
        client.get("/v1/whoami", headers=_bearer(issue_token(MAIN_USER)))
    access = [e for e in logs if e.get("event") == "http_request"]
    assert access, "没有产生访问日志"
    assert access[-1]["user_id"] == MAIN_USER
    assert str(access[-1]["request_id"]).startswith("req_")


def test_auth_failure_is_logged_without_the_token(client: TestClient) -> None:
    """脱敏：token 本身绝不能进日志。"""
    token = issue_token(MAIN_USER)
    with structlog.testing.capture_logs() as logs:
        client.get("/v1/whoami", headers={"Authorization": f"Bearer {token}x"})
    rendered = repr(logs)
    assert "auth_failed" in rendered
    assert token not in rendered


# ---- 结构性约束 ----


def test_all_business_routes_live_under_v1(app: FastAPI) -> None:
    """FR-108：除运维接口外，所有业务路由必须挂在 /v1 下。

    只看 `APIRoute`：框架自带的 docs / openapi 路由不算业务接口。
    """
    paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
    business = {p for p in paths if p not in PUBLIC_PATHS and not p.startswith("/v1")}
    assert not business, f"以下路由不在 /v1 下：{business}"


def test_public_paths_skip_authentication(client: TestClient) -> None:
    for path in ("/health", "/metrics"):
        assert client.get(path).status_code == 200


def test_unknown_route_under_v1_returns_404_not_500(client: TestClient) -> None:
    resp = client.get("/v1/nope", headers=_bearer(issue_token(MAIN_USER)))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_validation_error_returns_400_envelope(client: TestClient) -> None:
    resp = client.post("/v1/_probe", headers=_bearer(issue_token(MAIN_USER)), json={"user_id": "x"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    # 只回字段位置与错误类型，不回收到的原值
    assert all(set(d) == {"loc", "type"} for d in body["error"]["details"])
