"""测试环境隔离：**不读 `.env` 的数据库与向量配置**。

两次事故都出在同一个地方：测试跑在"本机 `.env` 恰好是什么"上。
JWT 那次是密钥长度，这次是 `EMBEDDING_PROVIDER=openai` + `RAG_TAU_HIGH=0.60`
把退款正样本切成了低置信转人工（fake 下 0.28/0.40 全绿，openai 下正样本 0.52–0.77）。

所以这里把三件事钉死，全局生效：

1. **独立数据库**：`cs_agent_pytest_<仓库路径哈希>`，每个 worktree 一个，
   与开发库、demo 库、`test_migrations` 的一次性库都不同名——测试灌进去的
   fake 向量污染不到任何人；
2. **provider 固定 `fake`**：确定性哈希向量，不触网，与灌库用的是同一个；
3. **τ 固定为 fake 的标定值**：判定不随 `.env` 漂移。

数据库不可达时不建库，交给各测试自己 skip。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from cs_agent.db import base as db_base
from cs_agent.rag.provider import FAKE_TAU_HIGH, FAKE_TAU_LOW
from cs_agent.settings import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_NAME = "cs_agent_pytest_" + hashlib.sha1(str(REPO_ROOT).encode()).hexdigest()[:8]


def _ensure_database(admin_url: str) -> bool:
    """建库（若不存在）。连不上返回 False，测试各自 skip。"""
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB_NAME}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
        return True
    except OperationalError:  # pragma: no cover - 取决于本机环境
        return False
    finally:
        admin.dispose()


def _prepare_schemas(url: str) -> None:
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS biz"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS agent"))
        conn.commit()
    engine.dispose()


def _install_test_env() -> str:
    """在**任何测试模块导入之前**改环境变量，然后清掉 Settings 的缓存。

    走环境变量而不是打补丁：pydantic-settings 里环境变量优先于 `.env`，
    而 `get_settings` 是 `lru_cache`——给它塞 `__wrapped__` 不起作用
    （缓存调用的是装饰时捕获的原函数），这一点踩过一次，写在这里免得再踩。
    """
    base_url = Settings().database_url
    url_str = make_url(base_url).set(database=TEST_DB_NAME).render_as_string(hide_password=False)

    os.environ["DATABASE_URL"] = url_str
    os.environ["EMBEDDING_PROVIDER"] = "fake"
    os.environ["RAG_TAU_LOW"] = str(FAKE_TAU_LOW)
    os.environ["RAG_TAU_HIGH"] = str(FAKE_TAU_HIGH)
    # 环境里恰好有 key 也不许去打真实 embedding
    os.environ["OPENAI_API_KEY"] = ""

    get_settings.cache_clear()
    db_base.get_engine.cache_clear()
    db_base.get_session_factory.cache_clear()
    return base_url


_ADMIN_URL = _install_test_env()


@pytest.fixture(scope="session", autouse=True)
def isolated_database() -> Iterator[None]:
    """确保测试库与两个 schema 存在。连不上就什么都不做，各测试自己 skip。"""
    if _ensure_database(_ADMIN_URL):
        _prepare_schemas(get_settings().database_url)
    yield
