"""异步数据库引擎 - 基于 SQLAlchemy 2.0 async + aiosqlite

提供全局引擎和 AsyncSession 工厂，所有仓储类共享同一连接池。
启用 WAL 模式提升并发读写性能（与旧实现保持一致）。
"""

from pathlib import Path
from typing import Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

DB_PATH = Path("data/qingci-bot.db")

_engine: Optional[create_async_engine] = None
_session_factory: Optional[sessionmaker] = None


def _set_sqlite_pragma(dbapi_conn, _connection_record):
    """在每个底层连接上设置 SQLite PRAGMA（WAL 模式）"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine():
    """获取全局异步引擎（懒加载）"""
    global _engine
    if _engine is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(
            f"sqlite+aiosqlite:///{DB_PATH}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        # 在底层同步引擎上注册 connect 事件，设置 WAL 模式
        event.listen(_engine.sync_engine, "connect", _set_sqlite_pragma)
    return _engine


def get_session_factory() -> sessionmaker:
    """获取 AsyncSession 工厂（懒加载）"""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def init_db():
    """初始化数据库：创建表结构

    首次运行时建表，已存在的表不受影响（CREATE TABLE IF NOT EXISTS 语义）。
    Alembic 迁移用于后续 schema 演进。
    """
    # 确保所有模型被导入，以便 SQLModel.metadata 能发现它们
    from . import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def dispose_engine():
    """关闭引擎，释放连接池资源"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
