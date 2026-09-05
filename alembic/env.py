"""Alembic 运行环境。

- 连接串来自 `cs_agent.settings.get_settings().database_url`，不硬编码。
- `include_schemas=True`：同时管理 biz / agent 两个 schema。
- `version_table_schema="agent"`：迁移版本表放在 agent schema，不污染业务 schema。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from cs_agent.db.base import Base
from cs_agent.db.models import agent as _agent_models  # noqa: F401  注册 agent 表
from cs_agent.db.models import biz as _biz_models  # noqa: F401  注册 biz 表
from cs_agent.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
VERSION_TABLE_SCHEMA = "agent"


def _database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=VERSION_TABLE_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：真正执行迁移。"""
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        # 版本表位于 agent schema，需先保证 schema 存在（正常情况下 docker init 已建好，此处兜底）
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {VERSION_TABLE_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=VERSION_TABLE_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
