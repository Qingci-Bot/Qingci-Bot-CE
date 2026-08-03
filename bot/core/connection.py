"""OneBot 11 协议层 - 基于 aiocqhttp 的反向 WebSocket 实现

底层使用 aiocqhttp（nonebot 出品）处理 OneBot v11 协议：
- 反向 WebSocket 服务端（端点 /ws、/ws/event、/ws/api）
- 消息段解析、API 调用、access_token 校验
- 事件总线

OneBotConnection 作为外观层，保持与旧 API 兼容：
- on_event(handler) 注册事件处理器
- call_api(action, params) 调用 OneBot API
- send_msg / send_group_msg / send_private_msg 便捷方法
- is_connected / last_heartbeat 状态查询

OneBot 实现端（如 LLBot/NapCat）需连接 ws://host:port/ws。
"""

import asyncio
import logging
import time
from typing import Callable, Optional

from aiocqhttp import CQHttp

logger = logging.getLogger("qingci-bot.connection")


class OneBotConnection:
    """OneBot 11 反向 WebSocket 连接管理（基于 aiocqhttp）"""

    def __init__(self, host: str = "127.0.0.1", port: int = 3001, access_token: str = ""):
        self.host = host
        self.port = port
        self.access_token = access_token.strip() if access_token else ""

        # aiocqhttp 引擎（反向 WS 模式：不传 api_root）
        self._bot = CQHttp(
            import_name="qingci-bot.connection",
            access_token=self.access_token or None,
            api_timeout_sec=30,
        )
        self._server_task: Optional[asyncio.Task] = None
        self._running = False
        self._event_handlers: list[Callable] = []
        self._last_heartbeat = 0.0

        # 注册 aiocqhttp 事件转发
        self._register_aiocqhttp_hooks()

    # ============ 事件转发 ============

    def _register_aiocqhttp_hooks(self):
        """将 aiocqhttp 收到的事件转发给本类的 _event_handlers"""

        @self._bot.on_message
        async def _on_message(event):
            await self._dispatch_event(event)

        @self._bot.on_notice
        async def _on_notice(event):
            await self._dispatch_event(event)

        @self._bot.on_request
        async def _on_request(event):
            await self._dispatch_event(event)

        @self._bot.on_meta_event
        async def _on_meta(event):
            # 心跳更新
            if event.get("meta_event_type") == "heartbeat":
                self._last_heartbeat = time.time()
                logger.debug(f"心跳: {event.get('status', {})}")
                return
            # 生命周期事件（连接建立）
            if event.get("meta_event_type") == "lifecycle":
                logger.info(f"LLBot 生命周期事件: {event.get('sub_type', '')}")
            await self._dispatch_event(event)

    def on_event(self, handler: Callable):
        """注册事件处理器（兼容旧 API，自动去重避免重复注册）"""
        if handler not in self._event_handlers:
            self._event_handlers.append(handler)
        return handler

    async def _dispatch_event(self, event):
        """分发事件到所有处理器

        aiocqhttp 的 Event 继承自 dict，可直接当 dict 用。
        """
        # Event 是 dict 子类，直接传入
        raw = dict(event)
        for handler in list(self._event_handlers):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(raw)
                else:
                    handler(raw)
            except Exception:
                logger.exception("事件处理器异常")

    # ============ 生命周期 ============

    async def start(self):
        """启动反向 WebSocket 服务器（异步）"""
        self._running = True
        logger.info(f"OneBot WS 服务器启动: ws://{self.host}:{self.port}/ws")
        self._server_task = asyncio.create_task(
            self._bot.run_task(
                host=self.host,
                port=self.port,
            )
        )
        await asyncio.sleep(0.1)
        # 检查服务器是否启动失败（如端口被占用）
        if self._server_task.done():
            exc = self._server_task.exception()
            if exc:
                self._running = False
                raise RuntimeError(f"OneBot WS 服务器启动失败: {exc}")

    async def stop(self):
        """停止服务器，清理事件处理器"""
        self._running = False
        # 清理事件处理器，防止重启时重复注册
        self._event_handlers.clear()
        # 关闭 Quart server
        try:
            if self._server_task and not self._server_task.done():
                self._server_task.cancel()
                try:
                    await asyncio.wait_for(self._server_task, timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
        except Exception:
            logger.exception("停止 OneBot WS 服务器异常")
        self._server_task = None
        logger.info("OneBot WS 服务器已停止")

    # ============ API 调用 ============

    async def call_api(self, action: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
        """调用 OneBot API

        返回 API 响应中的 data 字段（aiocqhttp 已自动解包）。
        """
        try:
            result = await asyncio.wait_for(
                self._bot.call_action(action, **(params or {})),
                timeout=timeout,
            )
            return result if isinstance(result, dict) else {"data": result}
        except asyncio.TimeoutError:
            raise TimeoutError(f"API 调用超时: {action}")
        except Exception as e:
            logger.error(f"API 调用失败 {action}: {e}")
            raise

    # ============ 便捷 API ============

    async def send_private_msg(self, user_id: int, message: str) -> dict:
        """发送私聊消息"""
        return await self.call_api("send_private_msg", {
            "user_id": user_id,
            "message": message,
        })

    async def send_group_msg(self, group_id: int, message: str) -> dict:
        """发送群聊消息"""
        return await self.call_api("send_group_msg", {
            "group_id": group_id,
            "message": message,
        })

    async def send_msg(self, message_type: str, target_id: int, message: str) -> dict:
        """发送消息"""
        if message_type == "private":
            return await self.send_private_msg(target_id, message)
        elif message_type == "group":
            return await self.send_group_msg(target_id, message)
        else:
            raise ValueError(f"未知的 message_type: {message_type}")

    async def get_group_info(self, group_id: int) -> dict:
        return await self.call_api("get_group_info", {"group_id": group_id})

    async def get_group_member_info(self, group_id: int, user_id: int) -> dict:
        return await self.call_api("get_group_member_info", {
            "group_id": group_id,
            "user_id": user_id,
        })

    # ============ 状态 ============

    @property
    def is_connected(self) -> bool:
        """是否有 OneBot 客户端连接"""
        if not self._running:
            return False
        try:
            # 优先用公开属性，回退到内部属性
            clients = getattr(self._bot, "_wsr_api_clients", None)
            if clients is None:
                # 尝试通过 server_app 检查
                clients = getattr(self._bot, "wsr_api_clients", None)
            return bool(clients)
        except Exception:
            return False

    @property
    def last_heartbeat(self) -> float:
        return self._last_heartbeat

    @property
    def bot(self) -> CQHttp:
        """暴露底层 aiocqhttp 实例，供需要高级用法的调用方使用"""
        return self._bot
