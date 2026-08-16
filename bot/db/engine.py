"""异步数据库引擎 - 基于 SQLAlchemy 2.0 async + aiosqlite

提供全局引擎和 AsyncSession 工厂，所有仓储类共享同一连接池。
启用 WAL 模式提升并发读写性能。
首次运行使用 Alembic 迁移建表（若迁移脚本可用），否则回退到 create_all。
"""

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

# 数据库文件位于可写数据根目录（默认 app_root()/data，可用 --data-dir 覆盖以实现多实例隔离）
from ..paths import data_root

_engine: AsyncEngine | None = None
_session_factory: sessionmaker | None = None
_engine_lock = threading.Lock()
_session_factory_lock = threading.Lock()


def db_path() -> Path:
    """返回当前实例的 SQLite 数据库文件路径"""
    return data_root() / "qingci-bot.db"


def _set_sqlite_pragma(dbapi_conn, _connection_record):
    """在每个底层连接上设置 SQLite PRAGMA（WAL 模式 + 并发调优）"""
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        # WAL 下 NORMAL 安全且写入更快；busy_timeout 降低 "database is locked" 风险
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def get_engine():
    """获取全局异步引擎（懒加载，线程安全）"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                db = db_path()
                db.parent.mkdir(parents=True, exist_ok=True)
                engine = create_async_engine(
                    f"sqlite+aiosqlite:///{db}",
                    echo=False,
                    connect_args={"check_same_thread": False},
                )
                # 在底层同步引擎上注册 connect 事件，设置 WAL 模式
                event.listen(engine.sync_engine, "connect", _set_sqlite_pragma)
                _engine = engine
    return _engine


def get_session_factory() -> sessionmaker:
    """获取 AsyncSession 工厂（懒加载，线程安全）"""
    global _session_factory
    if _session_factory is None:
        with _session_factory_lock:
            if _session_factory is None:
                _session_factory = sessionmaker(
                    get_engine(),
                    class_=AsyncSession,
                    expire_on_commit=False,
                )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """可复用的 AsyncSession 作用域：成功提交、异常回滚、最后关闭

    仓储层统一通过本协程管理器访问会话，避免重复的 commit/rollback/close
    样板代码；也便于在单个事件处理中复用同一会话执行多次数据库操作。
    """
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db():
    """初始化数据库：创建表结构

    首次运行时建表（create_all），已存在的表不受影响。
    Alembic 迁移用于后续 schema 演进（手动执行 alembic upgrade head）。
    """
    # 确保所有模型被导入，以便 SQLModel.metadata 能发现它们。
    # 用 importlib 显式导入：pyflakes 会把副作用导入误报为未使用
    import importlib

    importlib.import_module("bot.db.models")

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def dispose_engine():
    """关闭引擎，释放连接池资源"""
    global _engine, _session_factory
    with _engine_lock:
        engine = _engine
        _engine = None
        _session_factory = None
    if engine is not None:
        await engine.dispose()
