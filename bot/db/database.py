"""数据库仓储层 - 基于 SQLModel + AsyncSession

保留旧 Database 类的公开 API，内部改用 SQLModel ORM 实现。
新增 sessions 相关方法为 LLM 会话持久化（Step 2）做准备。
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import func, select

from .engine import dispose_engine, get_session_factory, init_db
from .models import Message, PluginConfig, SessionHistory

logger = logging.getLogger("qingci-bot.db")


class Database:
    """SQLite 数据库仓储类"""

    def __init__(self, path: Optional[Path] = None):
        # path 参数保留为兼容签名，实际路径由 engine.py 统一管理
        # 旧代码 Database() / Database(some_path) 均可工作
        pass

    async def connect(self):
        """初始化数据库连接并建表"""
        await init_db()
        logger.info("数据库已连接")

    async def close(self):
        """关闭数据库连接，释放连接池"""
        await dispose_engine()

    # ============ 消息记录 ============

    async def save_message(
        self,
        message_id: str,
        user_id: int,
        content: str,
        message_type: str = "group",
        group_id: Optional[int] = None,
        role: str = "user",
    ):
        """保存一条消息记录"""
        async with get_session_factory()() as session:
            session.add(
                Message(
                    message_id=message_id,
                    user_id=user_id,
                    content=content,
                    message_type=message_type,
                    group_id=group_id,
                    role=role,
                )
            )
            await session.commit()

    async def get_history(
        self,
        user_id: int,
        group_id: Optional[int] = None,
        limit: int = 20,
    ) -> list[dict]:
        """获取对话历史（按时间正序返回最近 limit 条）"""
        async with get_session_factory()() as session:
            if group_id:
                stmt = (
                    select(Message)
                    .where(Message.group_id == group_id, Message.user_id == user_id)
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
            else:
                stmt = (
                    select(Message)
                    .where(Message.user_id == user_id, Message.group_id.is_(None))
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
            rows = (await session.execute(stmt)).scalars().all()
            # 反转为正序，保持与旧 API 一致
            return [row.dict() for row in reversed(rows)]

    async def search_messages(
        self,
        keyword: str = "",
        user_id: Optional[int] = None,
        group_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """搜索消息记录"""
        async with get_session_factory()() as session:
            stmt = select(Message)
            if keyword:
                stmt = stmt.where(Message.content.contains(keyword))
            if user_id:
                stmt = stmt.where(Message.user_id == user_id)
            if group_id:
                stmt = stmt.where(Message.group_id == group_id)
            stmt = (
                stmt.order_by(Message.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [row.dict() for row in rows]

    async def get_message_count(self) -> int:
        """获取消息总数"""
        async with get_session_factory()() as session:
            stmt = select(func.count(Message.id))
            result = await session.execute(stmt)
            return result.scalar() or 0

    # ============ LLM 会话持久化（新增，为 Step 2 准备）============

    async def save_session(self, session_key: str, role: str, content: str):
        """保存一条 LLM 会话历史"""
        async with get_session_factory()() as session:
            session.add(
                SessionHistory(
                    session_key=session_key,
                    role=role,
                    content=content,
                )
            )
            await session.commit()

    async def get_sessions(self, session_key: str, limit: int = 40) -> list[dict]:
        """获取指定会话的历史（按时间正序返回最近 limit 条）"""
        async with get_session_factory()() as session:
            stmt = (
                select(SessionHistory)
                .where(SessionHistory.session_key == session_key)
                .order_by(SessionHistory.created_at.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [row.dict() for row in reversed(rows)]

    async def clear_sessions(self, session_key: Optional[str] = None):
        """清除会话历史：指定 key 清除单会话，None 清除全部"""
        async with get_session_factory()() as session:
            if session_key:
                stmt = select(SessionHistory).where(
                    SessionHistory.session_key == session_key
                )
            else:
                stmt = select(SessionHistory)
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                await session.delete(row)
            await session.commit()

    # ============ 插件配置 ============

    async def get_plugin_config(self, key: str) -> Optional[str]:
        """读取插件配置"""
        async with get_session_factory()() as session:
            stmt = select(PluginConfig).where(PluginConfig.key == key)
            row = (await session.execute(stmt)).scalar_one_or_none()
            return row.value if row else None

    async def set_plugin_config(self, key: str, value: str):
        """写入插件配置（upsert）"""
        async with get_session_factory()() as session:
            existing = (
                await session.execute(
                    select(PluginConfig).where(PluginConfig.key == key)
                )
            ).scalar_one_or_none()
            if existing:
                existing.value = value
                existing.updated_at = datetime.utcnow()
            else:
                session.add(PluginConfig(key=key, value=value))
            await session.commit()
