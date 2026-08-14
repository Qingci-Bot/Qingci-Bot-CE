"""Alembic 异步迁移环境

与 bot/db/engine.py 共用同一数据库 URL，确保迁移与应用使用一致的连接配置。
处理异步事件循环嵌套问题：当迁移在已有事件循环中被调用时（如应用启动时），
避免 asyncio.run() 抛 RuntimeError。
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 加载 alembic.ini 日志配置
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 导入所有 SQLModel 模型，让 autogenerate 能发现表结构
from bot.db import models  # noqa: F401, E402
from bot.db.engine import DB_PATH  # noqa: E402

# 注入数据库 URL（与运行时一致）
config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{DB_PATH}")

# SQLModel.metadata 作为 autogenerate 的目标
target_metadata = models.SQLModel.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite 需要 batch 模式支持 ALTER
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在给定连接上执行迁移"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite 需要 batch 模式
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步在线模式：创建临时引擎执行迁移"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """在在线模式下运行迁移"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中的事件循环，安全使用 asyncio.run
        asyncio.run(run_async_migrations())
        return
    # 已在事件循环中，调用方应直接 await run_async_migrations()
    raise RuntimeError("迁移在运行中的事件循环内被触发，请直接 await run_async_migrations()")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
