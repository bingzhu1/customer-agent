"""Alembic 迁移可逆性：downgrade base → upgrade head 不报错，且表齐全。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.exc import OperationalError

from cs_agent.seed.biz_seed import run_seed
from cs_agent.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


@pytest.fixture(scope="module")
def migrated_engine() -> Engine:
    """连接 .env 中的 DATABASE_URL 并升级到 head；连不上则 skip 本文件全部测试。"""
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    command.upgrade(_alembic_config(), "head")
    return engine


BIZ_TABLES = {"users", "orders", "order_items", "shipments", "payments", "tickets", "refunds"}
AGENT_TABLES = {"eval_runs", "eval_results", "alembic_version"}


def test_downgrade_then_upgrade_roundtrip(migrated_engine: Engine) -> None:
    cfg = _alembic_config()
    command.downgrade(cfg, "base")
    insp = inspect(migrated_engine)
    assert not BIZ_TABLES & set(insp.get_table_names(schema="biz"))
    assert not {"eval_runs", "eval_results"} & set(insp.get_table_names(schema="agent"))

    command.upgrade(cfg, "head")
    insp = inspect(migrated_engine)
    assert BIZ_TABLES <= set(insp.get_table_names(schema="biz"))
    assert AGENT_TABLES <= set(insp.get_table_names(schema="agent"))
    # 版本表必须在 agent schema，而不是 public
    assert "alembic_version" not in insp.get_table_names(schema="public")

    # 恢复数据，避免测试跑完后本机库是空的
    run_seed(migrated_engine)


def test_expected_indexes_exist(migrated_engine: Engine) -> None:
    insp = inspect(migrated_engine)
    expected = {
        ("biz", "orders", "user_id"),
        ("biz", "order_items", "order_id"),
        ("biz", "shipments", "order_id"),
        ("biz", "payments", "order_id"),
        ("biz", "tickets", "user_id"),
        ("biz", "refunds", "order_id"),
        ("agent", "eval_results", "run_id"),
    }
    for schema, table, column in expected:
        indexed_cols = {tuple(ix["column_names"]) for ix in insp.get_indexes(table, schema=schema)}
        assert (column,) in indexed_cols, f"{schema}.{table}({column}) 缺少索引"
