"""会话接口（FR-101/102/104）与 dev token。用假 LLM，不打网络；需要本机 Postgres。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from cs_agent.api.main import create_app
from cs_agent.eval.protocol import Usage
from cs_agent.graph.llm import Understanding
from cs_agent.rag.embeddings import FakeEmbeddings
from cs_agent.rag.ingest import ingest_policies
from cs_agent.seed.biz_seed import run_seed
from cs_agent.services import chat as chat_service
from cs_agent.settings import get_settings

MAIN_USER = 101
OTHER_USER = 202


class StubLlm:
    model = "fake"

    def understand(self, text: str) -> tuple[Understanding, Usage]:
        import re

        order = re.search(r"订单\s*(\d+)", text)
        return (
            Understanding(
                intent="refund_request" if "退款" in text else "order_status",
                order_id=int(order.group(1)) if order else None,
            ),
            Usage(llm_calls=1, input_tokens=100, output_tokens=20, models=["fake"]),
        )

    def respond(self, prompt: str) -> tuple[str, Usage]:
        return "好的。", Usage(llm_calls=1, input_tokens=200, output_tokens=30, models=["fake"])


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    run_seed(engine)
    ingest_policies(provider=FakeEmbeddings(), engine=engine)

    original = chat_service.ChatService._ensure_llm
    chat_service.ChatService._ensure_llm = lambda self: StubLlm()  # type: ignore[assignment,method-assign,return-value]
    with TestClient(create_app()) as c:
        yield c
    chat_service.ChatService._ensure_llm = original  # type: ignore[method-assign]


def _token(client: TestClient, user_id: int) -> str:
    resp = client.post("/v1/dev/token", json={"user_id": user_id})
    assert resp.status_code == 200
    return str(resp.json()["token"])


def _auth(client: TestClient, user_id: int = MAIN_USER) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(client, user_id)}"}


def _new_thread(client: TestClient, user_id: int = MAIN_USER) -> tuple[str, dict[str, str]]:
    headers = _auth(client, user_id)
    resp = client.post("/v1/threads", headers=headers)
    assert resp.status_code == 201
    return resp.json()["thread_id"], headers


# ---- dev token ----


def test_dev_token_needs_no_auth_and_binds_the_requested_user(client: TestClient) -> None:
    """dev 环境专用；生产不注册这个路由。"""
    token = _token(client, MAIN_USER)
    who = client.get("/v1/whoami", headers={"Authorization": f"Bearer {token}"})
    assert who.json()["user_id"] == MAIN_USER


# ---- FR-101 / FR-102 ----


def test_create_thread_returns_201_and_thread_id(client: TestClient) -> None:
    thread_id, _ = _new_thread(client)
    assert thread_id


def test_send_message_returns_prd_82_shape(client: TestClient) -> None:
    thread_id, headers = _new_thread(client)
    resp = client.post(
        f"/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"message": "订单 82913 现在什么状态？"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "thread_id",
        "reply",
        "decision",
        "reason_code",
        "confidence",
        "citations",
        "tools_used",
        "pending_action",
        "handoff_offer",
        "usage",
        "latency_ms",
        "request_id",
    }
    assert body["tools_used"] == ["get_order"]
    assert set(body["usage"]) == {"input_tokens", "output_tokens", "estimated_cost_usd"}
    assert body["pending_action"] is None  # 写路径 Phase 4 才开


def test_refund_of_foreign_order_is_denied(client: TestClient) -> None:
    """越权在接口层同样拦住：90210 属于 202。"""
    thread_id, headers = _new_thread(client)
    body = client.post(
        f"/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"message": "订单 90210 我要退款。"},
    ).json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "OWNERSHIP_MISMATCH"
    assert "90210" not in body["reply"]


def test_body_identity_fields_are_rejected(client: TestClient) -> None:
    """请求体里塞身份字段：schema 直接拒（extra=forbid），不是"忽略后继续"。"""
    thread_id, headers = _new_thread(client)
    resp = client.post(
        f"/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"message": "你好", "user_id": OTHER_USER},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


# ---- FR-104 ----


def test_get_thread_returns_messages_and_case_facts(client: TestClient) -> None:
    thread_id, headers = _new_thread(client)
    client.post(f"/v1/threads/{thread_id}/messages", headers=headers, json={"message": "你好"})
    body = client.get(f"/v1/threads/{thread_id}", headers=headers).json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    # CaseFacts 只由确定性代码写入，本 milestone 还没接线，应为空壳而不是缺字段
    assert body["case_facts"]["order_ids"] == []
    assert body["narrative_summary"] is None


def test_other_users_thread_returns_404(client: TestClient) -> None:
    thread_id, _ = _new_thread(client, MAIN_USER)
    other = _auth(client, OTHER_USER)
    assert client.get(f"/v1/threads/{thread_id}", headers=other).status_code == 404


def test_unknown_thread_and_foreign_thread_are_indistinguishable(client: TestClient) -> None:
    """FR-804 的口径延伸到会话：两者返回完全相同的 404 信封。"""
    thread_id, _ = _new_thread(client, MAIN_USER)
    other = _auth(client, OTHER_USER)
    foreign = client.get(f"/v1/threads/{thread_id}", headers=other)
    missing = client.get("/v1/threads/00000000-0000-4000-8000-000000000000", headers=other)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]


def test_posting_to_foreign_thread_returns_404(client: TestClient) -> None:
    thread_id, _ = _new_thread(client, MAIN_USER)
    other = _auth(client, OTHER_USER)
    resp = client.post(f"/v1/threads/{thread_id}/messages", headers=other, json={"message": "你好"})
    assert resp.status_code == 404


def test_threads_require_authentication(client: TestClient) -> None:
    assert client.post("/v1/threads").status_code == 401


# ---- 前端对接（CORS / pending_action / dev token 字段名）----


def test_cors_preflight_allows_vite_dev_origin(client: TestClient) -> None:
    """预检请求不带 token，必须不被认证中间件拦掉。"""
    resp = client.options(
        "/v1/threads",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "authorization" in resp.headers["access-control-allow-headers"].lower()


def test_cors_exposes_request_id_header(client: TestClient) -> None:
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert "x-request-id" in resp.headers["access-control-expose-headers"].lower()


def test_dev_token_response_field_is_token(client: TestClient) -> None:
    body = client.post("/v1/dev/token", json={"user_id": MAIN_USER}).json()
    assert set(body) == {"token", "token_type", "expires_in_minutes"}


def test_pending_action_shape_on_require_confirmation(client: TestClient) -> None:
    """82913：12 天前签收、未使用、89 元 → REQUIRE_CONFIRMATION，给出 §8.3 结构。"""
    thread_id, headers = _new_thread(client)
    body = client.post(
        f"/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"message": "订单 82913 我要退款。"},
    ).json()
    assert body["decision"] == "REQUIRE_CONFIRMATION"
    action = body["pending_action"]
    assert set(action) == {
        "action_id",
        "type",
        "summary",
        "policy_id",
        "policy_version",
        "confirm_url",
        "expires_at",
    }
    assert action["type"] == "refund"
    assert action["summary"] == {"order_id": 82913, "amount": "89.00", "currency": "CNY"}
    assert action["policy_id"] == "REFUND-STD-001"
    # 写路径未开：绝不编造 action_id / confirm_url，否则"确认"会指向不存在的动作
    assert action["action_id"] is None
    assert action["confirm_url"] is None


def test_no_pending_action_when_denied(client: TestClient) -> None:
    thread_id, headers = _new_thread(client)
    body = client.post(
        f"/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"message": "订单 82915 我要退款。"},
    ).json()
    assert body["decision"] == "DENY"
    assert body["pending_action"] is None


def test_case_facts_are_persisted_across_turns(client: TestClient) -> None:
    """⑤ 接线的端到端证据：CaseFacts 真的写进了 agent.case_state，第 2 轮读得到。"""
    thread_id, headers = _new_thread(client)
    client.post(
        f"/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"message": "订单 82913 现在什么状态？"},
    )
    body = client.get(f"/v1/threads/{thread_id}", headers=headers).json()
    assert body["case_facts"]["order_ids"] == [82913]
    assert body["case_facts"]["last_updated_by"].startswith("tool:")
