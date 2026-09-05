"""Alembic 迁移可逆性：downgrade base → upgrade head 不报错，且表齐全。

在**一次性库**上做，不碰 .env 指向的开发库——否则每跑一次测试，
开发库里的 eval_runs 历史与 seed 数据都会被清掉。
库名带仓库路径哈希（多个 worktree 各用各的，迁移 head 不同不会互相污染），
且每次 fixture 开始先 DROP 再 CREATE，保证从空库出发。
"""

import hashlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from cs_agent.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_NAME = "cs_agent_test_" + hashlib.sha1(str(REPO_ROOT).encode()).hexdigest()[:8]


def _test_url() -> str:
    url = make_url(get_settings().database_url).set(database=TEST_DB_NAME)
    return url.render_as_string(hide_password=False)  # str(url) 会把密码打码成 ***


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _test_url())
    return cfg


@pytest.fixture(scope="module")
def migrated_engine() -> Engine:
    """重建本 worktree 专属的一次性库并升级到 head；连不上数据库则 skip 本文件。"""
    admin = create_engine(get_settings().database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过数据库测试：{exc.__class__.__name__}")
    finally:
        admin.dispose()

    engine = create_engine(_test_url(), pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS biz"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS agent"))
        conn.commit()
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
