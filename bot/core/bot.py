"""Bot 主类 - 生命周期管理、组件编排"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

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
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    # ============ 生命周期 ============

    async def start(self) -> None:
        """启动 Bot"""
        logger.info("Qingci-Bot 启动中...")
        await self.db.connect()
        await self.plugin_manager.load_builtin(self)
        self.connection.on_event(self._handle_event)
        try:
            await self.connection.start()
        except Exception:
            # 连接启动失败，清理已初始化的资源
            try:
                await self.plugin_manager.shutdown()
            except Exception:
                logger.exception("清理插件失败")
            try:
                await self.db.close()
            except Exception:
                logger.exception("清理数据库失败")
            raise
        self._running = True
        logger.info("Qingci-Bot 启动成功")

    async def stop(self) -> None:
        """停止 Bot"""
        if not self._running and not self.connection.is_connected:
            # 完全未启动，无需停止
            return
        logger.info("Qingci-Bot 停止中...")
        self._running = False

        # 先停止接收新事件
        try:
            await self.connection.stop()
        except (Exception, asyncio.CancelledError):
            logger.exception("OneBot 连接停止异常")

        # 等待进行中的事件处理完成（最多 5 秒）
        if self._pending_tasks:
            logger.info(f"等待 {len(self._pending_tasks)} 个事件处理完成...")
            done, pending = await asyncio.wait(
                self._pending_tasks, timeout=5
            )
            for task in pending:
                task.cancel()
            # 等待取消的任务完成，避免 "Task destroyed while pending" 警告
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._pending_tasks.clear()

        try:
            await self.plugin_manager.shutdown()
        except (Exception, asyncio.CancelledError):
            logger.exception("插件卸载异常")

        try:
            await self.llm.close()
        except (Exception, asyncio.CancelledError):
            logger.exception("LLM 关闭异常")

        try:
            await self.db.close()
        except (Exception, asyncio.CancelledError):
            logger.exception("数据库关闭异常")

        logger.info("Qingci-Bot 已停止")

    # ============ 事件处理 ============

    async def _handle_event(self, event: dict) -> None:
        """处理 OneBot 事件 - 创建独立任务避免 stop() 死锁"""
        if not self._running:
            return
        task = asyncio.create_task(self._process_event(event))
        self._pending_tasks.add(task)

        def _cleanup(t: asyncio.Task[Any]) -> None:
            self._pending_tasks.discard(t)
            if t.exception():
                logger.exception("事件处理任务异常", exc_info=t.exception())

        task.add_done_callback(_cleanup)

    async def _process_event(self, event: dict) -> None:
        """实际事件处理逻辑"""
        try:
            ctx = self.dispatcher.dispatch(event)
            if ctx is None:  # 防御性检查，dispatch 当前总返回非 None
                return

            post_type = ctx.post_type or event.get("post_type", "")

            if post_type != "message":
                matcher_result = await self.dispatcher._run_event_matchers(self, event, ctx)
                if matcher_result is not None:
                    # Matcher 已处理，跳过旧式回调
                    return
                # 旧式回调 fallback
                for plugin in list(self.plugin_manager.plugins.values()):
                    if plugin.matchers:
                        continue
                    try:
                        if post_type == "notice":
                            await plugin.on_notice(event)
                        elif post_type == "request":
                            approve = await plugin.on_request(event)
                            if approve is not None:
                                await self._handle_request_approval(event, approve)
                                break  # request 已审批，跳出循环
                    except Exception:
                        logger.exception(
                            f"插件处理异常: {plugin.name}, "
                            f"post_type={post_type}, "
                            f"event_summary={self._event_summary(event)}"
                        )
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
                    logger.exception(
                        f"插件处理异常: {plugin.name}, "
                        f"post_type={post_type}, "
                        f"event_summary={self._event_summary(event)}"
                    )
        except Exception:
            logger.exception(f"处理事件异常: {event.get('post_type', 'unknown')}")

    @staticmethod
    def _event_summary(event: dict) -> str:
        """生成事件摘要（用于日志）"""
        return (
            f"user_id={event.get('user_id')}, "
            f"group_id={event.get('group_id')}, "
            f"message_type={event.get('message_type')}, "
            f"request_type={event.get('request_type')}, "
            f"notice_type={event.get('notice_type')}"
        )

    async def _send_reply(self, ctx: MessageContext, reply: str) -> None:
        """发送插件回复"""
        target_id = ctx.group_id if ctx.message_type == "group" else ctx.user_id
        if not target_id:
            logger.warning(
                f"无法发送回复：target_id 为空 (type={ctx.message_type}, "
                f"user_id={ctx.user_id}, group_id={ctx.group_id})"
            )
            return
        if ctx.message_type == "group" and ctx.user_id:
            prefix = ""
            if ctx.message_id:
                prefix += MessageDispatcher.build_cq_reply(ctx.message_id)
            prefix += MessageDispatcher.build_cq_at(ctx.user_id)
            reply = prefix + " " + reply

        for attempt in range(3):
            try:
                await self.connection.send_msg(ctx.message_type, target_id, reply)
                return
            except Exception:
                logger.exception(
                    f"发送消息失败 (attempt {attempt + 1}/3, "
                    f"type={ctx.message_type}, target={target_id})"
                )
                if attempt < 2:
                    await asyncio.sleep(0.5)

    async def _handle_request_approval(self, event: dict, approve: bool) -> None:
        """处理加好友/加群请求的审批结果"""
        try:
            request_type = event.get("request_type", "")
            flag = event.get("flag", "")
            if not flag:
                return
            if request_type == "friend":
                await self.connection.call_api(
                    "set_friend_add_request", {"flag": flag, "approve": approve}
                )
            elif request_type == "group":
                sub_type = event.get("sub_type", "")
                await self.connection.call_api(
                    "set_group_add_request",
                    {"flag": flag, "sub_type": sub_type, "approve": approve},
                )
        except Exception:
            logger.exception("处理请求审批失败")

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