"""Bot 主类 - 生命周期管理、组件编排"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from ..config import ConfigManager
from ..db import Database
from .connection import OneBotConnection
from .dispatcher import MessageDispatcher, MessageContext
from ..llm import LLMManager
from ..plugin import PluginManager

logger = logging.getLogger("qingci-bot")


class QingciBot:
    """Qingci-Bot 主类"""

    def __init__(self, config_path: Optional[str] = None):
        path = Path(config_path) if config_path else None
        self.config = ConfigManager(path)
        self.config.load()

        self.db = Database()
        self.connection = OneBotConnection(
            host=self.config.onebot.host,
            port=self.config.onebot.port,
            access_token=self.config.onebot.access_token,
        )
        self.dispatcher = MessageDispatcher()
        self.llm = LLMManager(self.config.llm, db=self.db)
        self.plugin_manager = PluginManager()

        self._running = False
        self._pending_tasks: set[asyncio.Task] = set()

    # ============ 生命周期 ============

    async def start(self):
        """启动 Bot"""
        logger.info("Qingci-Bot 启动中...")

        # 初始化数据库
        await self.db.connect()
        logger.info("数据库已连接")

        # 加载内置插件
        await self.plugin_manager.load_builtin(self)
        logger.info(f"已加载 {len(self.plugin_manager.plugins)} 个插件")

        # 注册事件分发
        self.connection.on_event(self._handle_event)

        # 启动 OneBot WS 服务器
        await self.connection.start()

        self._running = True
        logger.info("Qingci-Bot 启动完成")

    async def stop(self):
        """停止 Bot"""
        if not self._running:
            return
        logger.info("Qingci-Bot 停止中...")
        self._running = False

        # 先停止接收新事件
        try:
            await self.connection.stop()
        except Exception:
            logger.exception("OneBot 连接停止异常")

        # 等待进行中的事件处理完成（最多 5 秒）
        if self._pending_tasks:
            logger.info(f"等待 {len(self._pending_tasks)} 个事件处理完成...")
            done, pending = await asyncio.wait(
                self._pending_tasks, timeout=5
            )
            for task in pending:
                task.cancel()
            self._pending_tasks.clear()

        try:
            await self.plugin_manager.shutdown()
        except Exception:
            logger.exception("插件卸载异常")

        try:
            await self.llm.close()
        except Exception:
            logger.exception("LLM 关闭异常")

        try:
            await self.db.close()
        except Exception:
            logger.exception("数据库关闭异常")

        logger.info("Qingci-Bot 已停止")

    # ============ 事件处理 ============

    async def _handle_event(self, event: dict):
        """处理 OneBot 事件 - 创建独立任务避免 stop() 死锁"""
        if not self._running:
            return
        task = asyncio.create_task(self._process_event(event))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _process_event(self, event: dict):
        """实际事件处理逻辑"""
        ctx = await self.dispatcher.dispatch(event)
        if ctx is None:
            return

        post_type = ctx.post_type or event.get("post_type", "")

        if post_type != "message":
            await self.dispatcher._run_event_matchers(self, event, ctx)
            return

        reply = await self.dispatcher.run_matchers(self, event, ctx)
        if reply is not None:
            await self._send_reply(ctx, reply)
            return

        for plugin in list(self.plugin_manager.plugins.values()):
            if plugin.matchers:
                continue
            try:
                reply = await plugin.on_message(ctx)
                if reply:
                    await self._send_reply(ctx, reply)
                    break
            except Exception:
                logger.exception(f"插件处理异常: {plugin.name}")

    async def _send_reply(self, ctx: MessageContext, reply: str):
        """发送插件回复"""
        target_id = ctx.group_id if ctx.message_type == "group" else ctx.user_id
        if not target_id:
            logger.warning(f"无法发送回复：target_id 为空 (type={ctx.message_type})")
            return
        if ctx.message_type == "group" and ctx.user_id:
            reply = (
                MessageDispatcher.build_cq_reply(ctx.message_id)
                + MessageDispatcher.build_cq_at(ctx.user_id)
                + " " + reply
            )
        try:
            await self.connection.send_msg(ctx.message_type, target_id, reply)
        except Exception:
            logger.exception("发送消息失败")

    # ============ 状态 ============

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        """获取 Bot 状态"""
        return {
            "running": self._running,
            "connected": self.connection.is_connected,
            "last_heartbeat": self.connection.last_heartbeat,
            "plugins": [
                {"name": p.name, "version": p.version, "description": p.description}
                for p in self.plugin_manager.plugins.values()
            ],
        }


# ============ 全局实例 ============

_bot_instance: Optional[QingciBot] = None


def get_bot() -> QingciBot:
    """获取全局 Bot 实例"""
    if _bot_instance is None:
        raise RuntimeError("Bot 未初始化")
    return _bot_instance


def set_bot(bot: QingciBot):
    global _bot_instance
    _bot_instance = bot


def clear_bot():
    global _bot_instance
    _bot_instance = None