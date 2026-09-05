"""Repository 层身份 scope 强制（FR-803 / FR-804 / ADR-0008）。

需要本机 Postgres（DATABASE_URL 来自 .env）；不可达时 skip。
用到的 id 来自 `docs/phase0-fixtures.md` §2 / §3：
- 82913 属于 101；90210 属于 202；77777 根本不存在
- 工单 5001 属于 101；5003 属于 202；99999 不存在
"""

import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from cs_agent.auth.context import AuthContext, Role
from cs_agent.repositories.biz import BizRepository
from cs_agent.seed.biz_seed import run_seed
from cs_agent.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent

# 契约中的角色：101 是主角，202 拥有越权目标订单 90210
USER_MAIN = 101
USER_OTHER = 202
ORDER_OWNED = 82913
ORDER_OF_OTHER = 90210
ORDER_MISSING = 77777
TICKET_OWNED = 5001
TICKET_OF_OTHER = 5003
TICKET_MISSING = 99999


@pytest.fixture(scope="module")
def seeded_engine() -> Engine:
    """升级到 head 并灌 seed；连不上数据库则 skip 本文件全部测试。"""
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    run_seed(engine)
    return engine


@pytest.fixture
def session(seeded_engine: Engine) -> Iterator[Session]:
    with Session(seeded_engine) as s:
        yield s


def _repo(session: Session, user_id: int) -> BizRepository:
    return BizRepository(session, AuthContext.of(user_id, [Role.CUSTOMER]))


# ---- get_order ----


def test_get_order_own_returns_row(session: Session) -> None:
    order = _repo(session, USER_MAIN).get_order(ORDER_OWNED)
    assert order is not None
    assert order.user_id == USER_MAIN


def test_get_order_of_other_user_returns_none(session: Session) -> None:
    assert _repo(session, USER_MAIN).get_order(ORDER_OF_OTHER) is None


def test_get_order_missing_returns_none(session: Session) -> None:
    assert _repo(session, USER_MAIN).get_order(ORDER_MISSING) is None


def test_other_user_order_really_exists(session: Session) -> None:
    """越权返回 None 必须是"被 scope 挡住"，不是"数据本来就没有"。"""
    assert _repo(session, USER_OTHER).get_order(ORDER_OF_OTHER) is not None


def test_forbidden_and_missing_are_indistinguishable(session: Session) -> None:
    """FR-804：他人订单与不存在订单的返回值完全相同，无法据此枚举 id。"""
    repo = _repo(session, USER_MAIN)
    assert repo.get_order(ORDER_OF_OTHER) == repo.get_order(ORDER_MISSING) is None


# ---- get_shipping ----


def test_get_shipping_own_returns_row(session: Session) -> None:
    shipment = _repo(session, USER_MAIN).get_shipping(ORDER_OWNED)
    assert shipment is not None
    assert shipment.order_id == ORDER_OWNED


def test_get_shipping_of_other_user_returns_none(session: Session) -> None:
    """90210 确有物流记录，但不属于 101，一样查不到。"""
    assert _repo(session, USER_OTHER).get_shipping(ORDER_OF_OTHER) is not None
    assert _repo(session, USER_MAIN).get_shipping(ORDER_OF_OTHER) is None


def test_get_shipping_missing_returns_none(session: Session) -> None:
    assert _repo(session, USER_MAIN).get_shipping(ORDER_MISSING) is None


# ---- get_ticket ----


def test_get_ticket_own_returns_row(session: Session) -> None:
    ticket = _repo(session, USER_MAIN).get_ticket(TICKET_OWNED)
    assert ticket is not None
    assert ticket.user_id == USER_MAIN


def test_get_ticket_of_other_user_returns_none(session: Session) -> None:
    assert _repo(session, USER_OTHER).get_ticket(TICKET_OF_OTHER) is not None
    assert _repo(session, USER_MAIN).get_ticket(TICKET_OF_OTHER) is None


def test_get_ticket_missing_returns_none(session: Session) -> None:
    assert _repo(session, USER_MAIN).get_ticket(TICKET_MISSING) is None


# ---- 结构性红线 ----


@pytest.mark.parametrize("method_name", ["get_order", "get_shipping", "get_ticket"])
def test_repository_methods_take_no_identity_argument(method_name: str) -> None:
    """红线 1 / FR-208：身份只能来自 AuthContext，不得作为参数传入。"""
    params = set(inspect.signature(getattr(BizRepository, method_name)).parameters)
    assert not params & {"user_id", "tenant_id", "ctx", "auth"}


def test_auth_context_is_immutable() -> None:
    ctx = AuthContext.of(USER_MAIN)
    with pytest.raises(AttributeError):
        ctx.user_id = USER_OTHER  # type: ignore[misc]


def test_auth_context_rejects_unknown_role() -> None:
    with pytest.raises(ValueError):
        AuthContext.of(USER_MAIN, ["superuser"])
