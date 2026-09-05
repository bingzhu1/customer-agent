"""SQLAlchemy 2.0 基础设施：DeclarativeBase 与 engine / session 工厂。

连接串一律来自 `cs_agent.settings.get_settings().database_url`，不在此处硬编码。
"""

from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from cs_agent.settings import get_settings

# 统一命名约定，保证 Alembic autogenerate 与手写迁移的约束名一致
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型的公共基类。

    `biz` 与 `agent` 两个 schema 共用一份 metadata，便于 Alembic 统一管理。
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache
def get_engine() -> Engine:
    """进程级单例 engine。`pool_pre_ping` 避免长连接被数据库侧回收后报错。"""
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """返回 sessionmaker；调用方以 `with get_session_factory()() as session:` 方式使用。"""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
