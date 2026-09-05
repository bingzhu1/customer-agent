"""`RefundService(SIMULATED)`（FR-506）。

连 `.env` 的 DATABASE_URL（本 worktree 为 cs_agent_p3）；不可达时 skip 整个文件。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from cs_agent.db.models.biz import Refund
from cs_agent.domain.enums import ReasonCode, RefundStatus
from cs_agent.policy.engine import PolicyOutcome, PolicyVerdict
from cs_agent.services.refund import RefundService
from cs_agent.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
#: 契约 §2 的 82913：seed 里没有退款记录，用例可以自由增删
TEST_ORDER = 82913
TEST_USER = 101
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

ALLOW = PolicyVerdict(
    outcome=PolicyOutcome.ALLOW,
    reason_code=ReasonCode.POLICY_SATISFIED,
    policy_id="REFUND-STD-001",
    policy_version=3,
    max_auto_amount=Decimal("200"),
)


@pytest.fixture(scope="module")
def engine() -> Engine:
    """连不上数据库就 skip 整个文件，不让本地缺环境的人被红色淹没。"""
    eng = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    return eng


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _wipe(factory)
    with factory() as s:
        yield s
    _wipe(factory)


def _wipe(factory: sessionmaker[Session]) -> None:
    with factory() as s, s.begin():
        s.execute(delete(Refund).where(Refund.order_id == TEST_ORDER))


def _refunds(session: Session) -> list[Refund]:
    return list(session.scalars(select(Refund).where(Refund.order_id == TEST_ORDER)))


def test_writes_a_simulated_refund_with_the_policy_basis(session: Session) -> None:
    """审计要能回答"依据哪条规则退的"：policy_id / version / reason_code 一并落库（FR-507）。"""
    with session.begin():
        RefundService(session).execute_refund(
            order_id=TEST_ORDER,
            user_id=TEST_USER,
            amount=Decimal("89.00"),
            verdict=ALLOW,
            now=NOW,
        )
    rows = _refunds(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.simulated is True
    assert row.status == RefundStatus.SUCCEEDED.value
    assert row.amount == Decimal("89.00")
    assert row.user_id == TEST_USER
    assert (row.policy_id, row.policy_version) == ("REFUND-STD-001", 3)
    assert row.reason_code == ReasonCode.POLICY_SATISFIED.value
    assert row.created_at is not None and row.executed_at is not None


def test_amount_and_owner_come_from_the_caller_not_the_verdict(session: Session) -> None:
    """判定只提供依据；金额与归属由调用方（AuthContext + 业务库）给（红线 1）。"""
    with session.begin():
        RefundService(session).execute_refund(
            order_id=TEST_ORDER,
            user_id=TEST_USER,
            amount=Decimal("12.34"),
            verdict=ALLOW,
            now=NOW,
        )
    row = _refunds(session)[0]
    assert row.amount == Decimal("12.34")  # 不是 verdict 里的 max_auto_amount 200
    assert row.user_id == TEST_USER


def test_service_does_not_judge_eligibility(session: Session) -> None:
    """本服务**不是**防线：给它一个 DENY 判定，它照样落库。

    资格判断在策略引擎，放行与否在决策层与用户确认。把判断塞进这里会多出一条
    绕过策略引擎的路径（红线 2）。本用例固定这个分工，防止有人"顺手"在此加校验。
    """
    denied = PolicyVerdict(
        outcome=PolicyOutcome.DENY,
        reason_code=ReasonCode.POLICY_VIOLATION_WINDOW,
        policy_id="REFUND-STD-001",
        policy_version=3,
    )
    with session.begin():
        RefundService(session).execute_refund(
            order_id=TEST_ORDER,
            user_id=TEST_USER,
            amount=Decimal("1.00"),
            verdict=denied,
            now=NOW,
        )
    assert _refunds(session)[0].reason_code == ReasonCode.POLICY_VIOLATION_WINDOW.value


def test_flush_yields_an_id_without_committing(session: Session) -> None:
    """事务边界在调用方：服务只 flush 拿 id，回滚后库里不留痕。"""
    refund = RefundService(session).execute_refund(
        order_id=TEST_ORDER,
        user_id=TEST_USER,
        amount=Decimal("5.00"),
        verdict=ALLOW,
        now=NOW,
    )
    assert refund.id is not None
    session.rollback()
    assert _refunds(session) == []
