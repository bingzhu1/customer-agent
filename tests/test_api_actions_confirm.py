"""确认接口 `POST /v1/actions/{id}/confirm`（FR-503/504/505/602，PRD §5.3 第二段流）。

用假 LLM，不打网络；需要本机 Postgres（conftest 已把库指向独立的 pytest 库）。
这条链路是整个项目的收口：说要退款 → 系统判定 → 用户点确认 → 真的写退款、且只写一次。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from cs_agent.api.main import create_app
from cs_agent.db.models.agent import AgentAction, AuditLog
from cs_agent.db.models.biz import Refund
from cs_agent.rag.embeddings import FakeEmbeddings
from cs_agent.rag.ingest import ingest_policies
from cs_agent.seed.biz_seed import run_seed
from cs_agent.services import chat as chat_service
from cs_agent.settings import get_settings
from test_api_threads import OTHER_USER, StubLlm, _auth

#: 契约 §2 的 82913：89.00、12 天前签收、未使用 → REQUIRE_CONFIRMATION
REFUND_ORDER = 82913
MAIN_USER = 101


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    run_seed(eng)
    ingest_policies(provider=FakeEmbeddings(), engine=eng)
    return eng


@pytest.fixture(scope="module")
def client(engine: Engine) -> Iterator[TestClient]:
    original = chat_service.ChatService._ensure_llm
    chat_service.ChatService._ensure_llm = lambda self: StubLlm()  # type: ignore[assignment,method-assign,return-value]
    with TestClient(create_app()) as c:
        yield c
    chat_service.ChatService._ensure_llm = original  # type: ignore[method-assign]


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    _wipe(maker)
    yield maker
    _wipe(maker)


def _wipe(maker: sessionmaker[Session]) -> None:
    """只清本文件用到的订单相关行；biz 其余部分归 seed 所有。"""
    with maker() as s, s.begin():
        s.execute(delete(Refund).where(Refund.order_id == REFUND_ORDER))
        ids = list(s.scalars(select(AgentAction.id)))
        if ids:
            s.execute(delete(AuditLog).where(AuditLog.action_id.in_(ids)))
        s.execute(delete(AgentAction))


def _ask_for_refund(client: TestClient, user_id: int = MAIN_USER) -> dict[str, Any]:
    """走完整对话，拿回 pending_action。"""
    headers = _auth(client, user_id)
    thread_id = client.post("/v1/threads", headers=headers).json()["thread_id"]
    body = client.post(
        f"/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"message": f"订单 {REFUND_ORDER} 我要退款。"},
    ).json()
    assert body["decision"] == "REQUIRE_CONFIRMATION", body
    return dict(body["pending_action"])


def _refunds(maker: sessionmaker[Session]) -> list[Refund]:
    with maker() as s:
        return list(s.scalars(select(Refund).where(Refund.order_id == REFUND_ORDER)))


# --- 待确认动作确实落库了 ----------------------------------------------------------


def test_pending_action_carries_a_real_action_id(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """前端按 action_id 是否为空决定按钮能不能点，所以它必须是库里真实存在的行。"""
    action = _ask_for_refund(client)
    assert action["action_id"] is not None
    assert action["confirm_url"] == f"/v1/actions/{action['action_id']}/confirm"
    assert action["expires_at"] is not None

    with factory() as s:
        row = s.get(AgentAction, int(action["action_id"]))
    assert row is not None
    assert row.status == "awaiting_confirmation"
    assert row.user_id == MAIN_USER
    assert len(row.idempotency_key) == 64
    # 提议阶段绝不动业务库（红线 2）
    assert _refunds(factory) == []


def test_asking_twice_in_the_same_hour_reuses_one_action(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """同一小时内重复要求退同一单，回到同一个 action_id，不在队列里堆重复退款。"""
    first = _ask_for_refund(client)
    second = _ask_for_refund(client)
    assert first["action_id"] == second["action_id"]
    with factory() as s:
        assert len(list(s.scalars(select(AgentAction)))) == 1


# --- 确认执行（FR-503/602）-------------------------------------------------------


def test_confirm_executes_the_refund_once(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    action_id = _ask_for_refund(client)["action_id"]
    resp = client.post(
        f"/v1/actions/{action_id}/confirm", headers=_auth(client), json={"confirm": True}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["replay"] is False
    assert body["reason_code"] == "POLICY_SATISFIED"
    assert body["result"]["amount"] == "89.00"
    assert body["result"]["simulated"] is True

    refunds = _refunds(factory)
    assert len(refunds) == 1
    assert refunds[0].user_id == MAIN_USER
    assert (refunds[0].policy_id, refunds[0].policy_version) == ("REFUND-STD-001", 3)


def test_confirming_twice_never_refunds_twice(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """用户连点两次：第二次是重放，返回同一结果，库里仍然只有一条退款。"""
    action_id = _ask_for_refund(client)["action_id"]
    url = f"/v1/actions/{action_id}/confirm"
    first = client.post(url, headers=_auth(client), json={"confirm": True}).json()
    second = client.post(url, headers=_auth(client), json={"confirm": True}).json()

    assert (first["replay"], second["replay"]) == (False, True)
    assert second["reason_code"] == "IDEMPOTENT_REPLAY"
    assert second["result"] == first["result"]
    assert len(_refunds(factory)) == 1


def test_confirm_writes_an_audit_trail(client: TestClient, factory: sessionmaker[Session]) -> None:
    action_id = int(_ask_for_refund(client)["action_id"])
    client.post(f"/v1/actions/{action_id}/confirm", headers=_auth(client), json={"confirm": True})
    with factory() as s:
        events = list(
            s.scalars(
                select(AuditLog.event_type)
                .where(AuditLog.action_id == action_id)
                .order_by(AuditLog.id)
            )
        )
    assert events == ["action_proposed", "action_executed"]


# --- 放弃 -----------------------------------------------------------------------


def test_confirm_false_abandons_the_action(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    action_id = _ask_for_refund(client)["action_id"]
    body = client.post(
        f"/v1/actions/{action_id}/confirm",
        headers=_auth(client),
        json={"confirm": False, "note": "算了"},
    ).json()
    assert body["status"] == "rejected"
    assert _refunds(factory) == []

    # 放弃之后再确认：状态冲突，不是 500 也不是悄悄执行
    resp = client.post(
        f"/v1/actions/{action_id}/confirm", headers=_auth(client), json={"confirm": True}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ACTION_STATE_CONFLICT"
    assert _refunds(factory) == []


# --- 归属与存在性（FR-505、PRD §8.4）----------------------------------------------


def test_other_users_action_and_missing_action_return_the_same_404(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    """他人的动作与根本不存在的动作，**信封完全一致**——不能靠响应差异探测存在性。"""
    action_id = int(_ask_for_refund(client)["action_id"])

    theirs = client.post(
        f"/v1/actions/{action_id}/confirm",
        headers=_auth(client, OTHER_USER),
        json={"confirm": True},
    )
    missing = client.post(
        f"/v1/actions/{action_id + 10_000_000}/confirm",
        headers=_auth(client, OTHER_USER),
        json={"confirm": True},
    )

    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["error"] == missing.json()["error"]
    assert _refunds(factory) == []


def test_confirm_requires_a_token(client: TestClient) -> None:
    resp = client.post("/v1/actions/1/confirm", json={"confirm": True})
    assert resp.status_code == 401


# --- 过期（FR-504）---------------------------------------------------------------


def test_expired_action_returns_410(client: TestClient, factory: sessionmaker[Session]) -> None:
    """把 expires_at 拨到过去（等 24 小时不现实），确认应当拿到 410 而不是照样退钱。"""
    action_id = int(_ask_for_refund(client)["action_id"])
    with factory() as s, s.begin():
        s.execute(
            update(AgentAction)
            .where(AgentAction.id == action_id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )

    resp = client.post(
        f"/v1/actions/{action_id}/confirm", headers=_auth(client), json={"confirm": True}
    )
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "ACTION_EXPIRED"
    assert _refunds(factory) == []

    with factory() as s:
        row = s.get(AgentAction, action_id)
    assert row is not None and row.status == "expired"
