"""数据库仓储层 - 基于 SQLModel + AsyncSession

保留旧 Database 类的公开 API，内部改用 SQLModel ORM 实现。
新增 sessions 相关方法为 LLM 会话持久化（Step 2）做准备。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, func, select

from .engine import dispose_engine, get_session_factory, init_db
from .models import AuditLog, GroupConfig, Message, PluginConfig, SessionHistory, UsageLog

logger = logging.getLogger("qingci-bot.db")


class Database:
    """SQLite 数据库仓储类"""

    def __init__(self, path: Optional[str] = None):
        # path 参数保留为兼容签名，实际路径由 engine.py 统一管理
        pass

    async def connect(self) -> None:
        """初始化数据库连接并建表"""
        await init_db()
        logger.info("数据库已连接")

    async def close(self) -> None:
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
    ) -> None:
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
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.debug(f"消息已存在，跳过: {message_id}")

    async def get_history(
        self,
        user_id: int,
        group_id: Optional[int] = None,
        limit: int = 20,
    ) -> list[dict]:
        """获取对话历史（按时间正序返回最近 limit 条）"""
        async with get_session_factory()() as session:
            if group_id is not None:
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
            return [row.model_dump() for row in reversed(rows)]

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
                # 转义 LIKE 特殊字符
                escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                stmt = stmt.where(Message.content.like(f"%{escaped}%", escape="\\"))
            if user_id is not None:
                stmt = stmt.where(Message.user_id == user_id)
            if group_id is not None:
                stmt = stmt.where(Message.group_id == group_id)
            stmt = (
                stmt.order_by(Message.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [row.model_dump() for row in rows]

    async def get_message_count(self) -> int:
        """获取消息总数"""
        async with get_session_factory()() as session:
            stmt = select(func.count(Message.id))
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def clear_messages(
        self,
        user_id: Optional[int] = None,
        group_id: Optional[int] = None,
        before_days: Optional[int] = None,
    ) -> int:
        """按条件清理消息记录，返回删除条数。

        参数:
            user_id: 仅清理该用户的消息
            group_id: 仅清理该群的消息
            before_days: 仅清理 N 天前的消息
        """
        async with get_session_factory()() as session:
            stmt = delete(Message)
            if user_id is not None:
                stmt = stmt.where(Message.user_id == user_id)
            if group_id is not None:
                stmt = stmt.where(Message.group_id == group_id)
            if before_days is not None and before_days > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(days=before_days)
                stmt = stmt.where(Message.created_at < cutoff)
            result = await session.execute(stmt)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("清理消息记录失败，已回滚")
                raise
            logger.info(
                f"清理消息记录 {result.rowcount} 条 "
                f"(user_id={user_id}, group_id={group_id}, before_days={before_days})"
            )
            return result.rowcount or 0

    # ============ LLM 会话持久化（新增，为 Step 2 准备）============

    async def save_session(self, session_key: str, role: str, content: str) -> None:
        """保存一条 LLM 会话历史"""
        async with get_session_factory()() as session:
            session.add(
                SessionHistory(
                    session_key=session_key,
                    role=role,
                    content=content,
                )
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("会话历史写入失败，已回滚")
                raise

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
            return [row.model_dump() for row in reversed(rows)]

    async def clear_sessions(self, session_key: Optional[str] = None) -> None:
        """清除会话历史：指定 key 清除单会话，None 清除全部"""
        async with get_session_factory()() as session:
            if session_key:
                stmt = delete(SessionHistory).where(
                    SessionHistory.session_key == session_key
                )
            else:
                stmt = delete(SessionHistory)
            await session.execute(stmt)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("清除会话历史失败，已回滚")
                raise

    async def delete_last_session(self, session_key: str, role: str) -> None:
        """删除指定会话最后一条指定角色的记录（原子操作）"""
        async with get_session_factory()() as session:
            # 使用子查询原子删除最后一条匹配记录
            subq = (
                select(SessionHistory.id)
                .where(
                    SessionHistory.session_key == session_key,
                    SessionHistory.role == role,
                )
                .order_by(SessionHistory.created_at.desc(), SessionHistory.id.desc())
                .limit(1)
            )
            stmt = delete(SessionHistory).where(SessionHistory.id.in_(subq))
            await session.execute(stmt)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("删除最后一条会话记录失败，已回滚")
                raise

    async def trim_sessions(self, session_key: str, keep: int) -> None:
        """删除指定会话超出最近 keep 条的旧记录（防止会话表无界增长）"""
        async with get_session_factory()() as session:
            subq = (
                select(SessionHistory.id)
                .where(SessionHistory.session_key == session_key)
                .order_by(SessionHistory.created_at.desc(), SessionHistory.id.desc())
                .limit(keep)
            )
            stmt = delete(SessionHistory).where(
                SessionHistory.session_key == session_key,
                SessionHistory.id.not_in(subq),
            )
            await session.execute(stmt)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("裁剪会话历史失败，已回滚")
                raise

    # ============ 插件配置 ============

    async def get_plugin_config(self, key: str) -> Optional[str]:
        """读取插件配置"""
        async with get_session_factory()() as session:
            stmt = select(PluginConfig).where(PluginConfig.key == key)
            row = (await session.execute(stmt)).scalar_one_or_none()
            return row.value if row else None

    async def set_plugin_config(self, key: str, value: str) -> None:
        """写入插件配置（upsert）"""
        async with get_session_factory()() as session:
            existing = (
                await session.execute(
                    select(PluginConfig).where(PluginConfig.key == key)
                )
            ).scalar_one_or_none()
            if existing:
                existing.value = value
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(PluginConfig(key=key, value=value))
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("插件配置写入失败，已回滚")
                raise

    # ============ 群粒度配置 ============

    async def get_group_config(self, group_id: int) -> Optional[dict]:
        """获取群配置（未配置时返回 None）"""
        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(GroupConfig).where(GroupConfig.group_id == group_id)
                )
            ).scalar_one_or_none()
            return row.model_dump(mode="json") if row else None

    async def upsert_group_config(
        self,
        group_id: int,
        enabled: bool,
        trigger_mode: Optional[str] = None,
    ) -> None:
        """新增或更新群配置

        Args:
            enabled: 群内是否启用 Bot
            trigger_mode: 群触发模式（None 表示跟随全局）
        """
        async with get_session_factory()() as session:
            existing = (
                await session.execute(
                    select(GroupConfig).where(GroupConfig.group_id == group_id)
                )
            ).scalar_one_or_none()
            if existing:
                existing.enabled = enabled
                existing.trigger_mode = trigger_mode
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(
                    GroupConfig(
                        group_id=group_id,
                        enabled=enabled,
                        trigger_mode=trigger_mode,
                    )
                )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("群配置写入失败，已回滚")
                raise

    async def list_group_configs(self) -> list[dict]:
        """列出所有已配置的群（按 group_id 升序）"""
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(GroupConfig).order_by(GroupConfig.group_id)
                )
            ).scalars().all()
            return [row.model_dump(mode="json") for row in rows]

    # ============ 用量统计 ============

    async def save_usage(
        self,
        session_key: str,
        user_id: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        source: str = "chat",
    ) -> None:
        """记录一次 LLM 调用用量（source: chat/tool/summary/image）"""
        async with get_session_factory()() as session:
            session.add(
                UsageLog(
                    session_key=session_key,
                    user_id=user_id,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    source=source,
                )
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("用量记录写入失败，已回滚")
                raise

    async def get_usage_stats(self, days: int = 30) -> list[dict]:
        """按天聚合最近 days 天的用量（走 created_at 索引）

        返回：[{date, prompt_tokens, completion_tokens, calls}]，按日期升序
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with get_session_factory()() as session:
            day_col = func.date(UsageLog.created_at).label("day")
            stmt = (
                select(
                    day_col,
                    func.sum(UsageLog.prompt_tokens).label("prompt_tokens"),
                    func.sum(UsageLog.completion_tokens).label("completion_tokens"),
                    func.count(UsageLog.id).label("calls"),
                )
                .where(UsageLog.created_at >= cutoff)
                .group_by(day_col)
                .order_by(day_col)
            )
            rows = (await session.execute(stmt)).all()
            return [
                {
                    "date": row.day,
                    "prompt_tokens": row.prompt_tokens or 0,
                    "completion_tokens": row.completion_tokens or 0,
                    "calls": row.calls or 0,
                }
                for row in rows
            ]

    # ============ 审计日志 ============

    async def get_audit_logs(self, limit: int = 100) -> list[dict]:
        """获取审计日志（按时间倒序）"""
        async with get_session_factory()() as session:
            stmt = (
                select(AuditLog)
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [row.model_dump(mode="json") for row in rows]

    # ============ 消息分批导出 ============

    async def get_messages_batch(self, after_id: int = 0, limit: int = 1000) -> list[dict]:
        """按 id 游标分批获取消息（用于 CSV 流式导出）"""
        async with get_session_factory()() as session:
            stmt = (
                select(Message)
                .where(Message.id > after_id)
                .order_by(Message.id)
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [row.model_dump(mode="json") for row in rows]
