"""OneBot 11 WebSocket 连接层 - 反向 WS 模式

LLBot 作为客户端主动连接本框架的 WebSocket 服务器。
本模块负责：接收事件、发送 API 调用、心跳维持、access_token 校验。
"""

import asyncio
import json
import logging
import time
from typing import Callable, Optional

import websockets
from websockets.asyncio.server import ServerConnection

logger = logging.getLogger("qingci-bot.connection")


class OneBotConnection:
    """OneBot 11 反向 WebSocket 连接管理"""

    def __init__(self, host: str = "127.0.0.1", port: int = 3001, access_token: str = ""):
        self.host = host
        self.port = port
        self.access_token = access_token
        self._server = None
        self._connection: Optional[ServerConnection] = None
        self._running = False
        self._echo_counter = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._event_handlers: list[Callable] = []
        self._last_heartbeat = 0.0

    # ============ 事件回调 ============

    def on_event(self, handler: Callable):
        """注册事件处理器"""
        self._event_handlers.append(handler)
        return handler

    async def _dispatch_event(self, event: dict):
        """分发事件到所有处理器"""
        for handler in self._event_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception:
                logger.exception("事件处理器异常")

    # ============ 服务端 ============

    async def start(self):
        """启动 WebSocket 服务器"""
        self._running = True
        logger.info(f"OneBot WS 服务器启动: ws://{self.host}:{self.port}")
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            max_size=10 * 1024 * 1024,  # 10MB
            subprotocols=["11"],
        )

    async def stop(self):
        """停止服务器"""
        self._running = False
        if self._connection:
            try:
                await asyncio.wait_for(self._connection.close(), timeout=2)
            except Exception:
                pass
            self._connection = None
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=3)
            except asyncio.TimeoutError:
                pass
        # 清理未完成的请求
        pending = list(self._pending.items())
        self._pending.clear()
        for echo, future in pending:
            if not future.done():
                future.set_exception(ConnectionError("连接已断开"))
        logger.info("OneBot WS 服务器已停止")

    async def _handle_client(self, ws: ServerConnection):
        """处理 LLBot 客户端连接"""
        peer = ws.remote_address

        # access_token 校验
        if self.access_token:
            auth_header = ""
            if ws.request is not None:
                auth_header = ws.request.headers.get("Authorization", "")
            expected = f"Bearer {self.access_token}"
            if auth_header != expected:
                logger.warning(f"LLBot access_token 校验失败: {auth_header}")
                await ws.close(1008, "Invalid access token")
                return

        # 如果已有连接，关闭旧连接
        if self._connection is not None and self._connection != ws:
            logger.info("检测到新的 LLBot 连接，关闭旧连接")
            try:
                await self._connection.close()
            except Exception:
                pass

        self._connection = ws
        logger.info(f"LLBot 已连接: {peer}")

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"无效 JSON: {raw[:200]}")
                    continue

                await self._process_message(data)
        except websockets.ConnectionClosed as e:
            logger.info(f"LLBot 连接断开: {peer} (code={e.code})")
        except Exception:
            logger.exception(f"LLBot 连接处理异常: {peer}")
        finally:
            if self._connection == ws:
                self._connection = None
            # 取消属于本连接的所有待响应请求
            pending = list(self._pending.items())
            for echo, future in pending:
                if not future.done():
                    future.set_exception(ConnectionError("连接已断开"))
            self._pending.clear()

    async def _process_message(self, data: dict):
        """处理收到的消息"""
        # 心跳
        if data.get("meta_event_type") == "heartbeat":
            self._last_heartbeat = time.time()
            logger.debug(f"心跳: {data.get('status', {})}")
            return

        # API 响应（echo 匹配）
        echo = data.get("echo")
        if echo and echo in self._pending:
            future = self._pending.pop(echo)
            if not future.done():
                future.set_result(data)
            return

        # 事件
        post_type = data.get("post_type")
        if post_type:
            logger.debug(f"收到事件: {post_type} | {data.get('message_type', '')}")
            await self._dispatch_event(data)

    # ============ API 调用 ============

    async def call_api(self, action: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
        """调用 OneBot API"""
        if not self._connection or not self._running:
            raise ConnectionError("LLBot 未连接")

        self._echo_counter += 1
        echo = str(self._echo_counter)
        payload = {"action": action, "params": params or {}, "echo": echo}

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[echo] = future

        await self._connection.send(json.dumps(payload, ensure_ascii=False))

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(echo, None)
            raise TimeoutError(f"API 调用超时: {action}")

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
        """发送消息（自动判断私聊/群聊）"""
        if message_type == "private":
            return await self.send_private_msg(target_id, message)
        return await self.send_group_msg(target_id, message)

    async def get_group_info(self, group_id: int) -> dict:
        return await self.call_api("get_group_info", {"group_id": group_id})

    async def get_group_member_info(self, group_id: int, user_id: int) -> dict:
        return await self.call_api("get_group_member_info", {
            "group_id": group_id,
            "user_id": user_id,
        })

    @property
    def is_connected(self) -> bool:
        if not self._connection or not self._running:
            return False
        try:
            return self._connection.state.name == "OPEN"
        except Exception:
            return False

    @property
    def last_heartbeat(self) -> float:
        return self._last_heartbeat