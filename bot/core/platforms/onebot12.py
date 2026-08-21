"""OneBot 12 平台适配器 — 反向 WebSocket 原生对接

OneBot 12 实现端（如 NapCat / Lagrange.OneBot 等）作为客户端反向连接
本服务端（ws://host:port/），事件以 OneBot 12 标准格式直接推送，动作以
JSON-RPC（action/params/echo）请求调用。相比 v11 适配器（aiocqhttp +
v11_compat 翻译层），事件无需翻译直通 Dispatcher，回复段原生 v12 表达。

协议要点（https://12.onebot.dev/）：
- 握手：可选校验 Authorization: Bearer <access_token> 或 ?access_token=
- 事件：{id, impl, platform, self_id, time, type, detail_type, sub_type, ...}
- 动作请求：{action, params, echo?}
- 动作响应：{status, retcode, data, message, echo?}

实现说明：
- 多实现端可同时接入；动作请求默认发往最近活跃的连接
- 事件强制注入 platform=onebot12 供内部路由（覆盖实现端的 platform 字段，
  与 v11 适配器统一为 Qingci-Bot 侧的平台语义）
- 元事件（heartbeat/lifecycle）走 emit_metaevent 通道
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, cast

from aiohttp import WSMsgType, web

from .base import PlatformAdapter

logger = logging.getLogger("qingci-bot.platforms.onebot12")

#: 反向 WS 路径（OneBot 12 规范未强制，实现端按此配置 url）
_WS_PATH = "/"

#: WS 心跳间隔（秒）：aiohttp 自动 ping/pong 探活
_WS_HEARTBEAT = 30.0

#: 动作响应默认超时（秒）
_ACTION_TIMEOUT = 30.0


class OneBot12Adapter(PlatformAdapter):
    """OneBot 12 反向 WebSocket 服务端适配器（事件直通、动作 JSON-RPC）"""

    name = "onebot12"
    display_name = "OneBot 12"
    supports_request_approval = True  # 支持 friend_request.handle / group_request.handle

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3002,
        access_token: str = "",
        enabled: bool = True,
    ):
        super().__init__()
        self.host = host
        self.port = port
        self.access_token = access_token.strip() if access_token else ""
        self.enabled = enabled

        self._running = False
        self._connected = False
        self._last_heartbeat = 0.0
        self.self_id: str = ""
        self.impl: str = ""

        # aiohttp 服务
        self._app = web.Application()
        self._app.router.add_get(_WS_PATH, self._ws_handler)
        self._runner: web.AppRunner | None = None

        # 反向 WS 客户端连接表：ws -> {self_id, impl, last_active}
        self._clients: dict[web.WebSocketResponse, dict[str, Any]] = {}

        # JSON-RPC：echo -> Future（异步等待动作响应）
        self._pending: dict[str, asyncio.Future] = {}
        self._echo_seq = 0

    # ============ 生命周期 ============

    async def start(self) -> None:
        """启动反向 WS 服务器（绑定失败快速抛出）"""
        if not self.enabled:
            logger.info(
                "OneBot 12 已禁用（platforms.onebot12.enabled=false），跳过反向 WS 启动: %s:%s",
                self.host,
                self.port,
            )
            return
        try:
            self._runner = web.AppRunner(self._app, access_log=None)
            await self._runner.setup()
            site = web.TCPSite(self._runner, self.host, self.port)
            await site.start()
        except Exception as exc:
            self._running = False
            raise RuntimeError(f"OneBot 12 WS 服务器启动失败: {exc}") from exc
        self._running = True
        logger.info("OneBot 12 反向 WS 服务器已启动: ws://%s:%s%s", self.host, self.port, _WS_PATH)

    async def stop(self) -> None:
        """关闭全部客户端连接与服务器"""
        self._running = False
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("OneBot 12 连接已关闭"))
        self._pending.clear()
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                logger.exception("OneBot 12 服务器清理失败")
            self._runner = None
        self._connected = False
        logger.info("OneBot 12 反向 WS 服务器已停止")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_heartbeat(self) -> float:
        return self._last_heartbeat

    def status_info(self) -> dict:
        """扩展平台状态字段（合并进 get_status 的 platforms 项）"""
        return {
            "impl": self.impl,
            "client_count": len(self._clients),
            "connection_state": "connected"
            if self._connected
            else ("stopped" if not self._running else "idle"),
        }

    # ============ 反向 WS 处理 ============

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        """处理实现端的反向 WS 连接（事件推送 + 动作 JSON-RPC）"""
        if self.access_token:
            auth = request.headers.get("Authorization", "")
            provided = (
                auth[7:] if auth.startswith("Bearer ") else request.query.get("access_token", "")
            )
            if not provided or provided != self.access_token:
                logger.warning(
                    "OneBot 12 连接被拒绝：access_token 不匹配 (from %s)", request.remote
                )
                raise web.HTTPUnauthorized(text="invalid access token")

        ws = web.WebSocketResponse(heartbeat=_WS_HEARTBEAT)
        await ws.prepare(request)
        self._clients[ws] = {"self_id": "", "impl": "", "last_active": time.time()}
        # 接入通知：首个协议端接入广播 connected，已有连接在线时再次接入按重连语义
        # （与 telegram 适配器的连接通知写法一致）
        was_connected = self._connected
        self._connected = True
        if was_connected:
            await self.notify_reconnected()
        else:
            await self.notify_connected()
        logger.info(
            "OneBot 12 协议端接入 (from %s, clients=%d, protocol=%s)",
            request.remote,
            len(self._clients),
            request.headers.get("Sec-WebSocket-Protocol", ""),
        )
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        await self._handle_message(ws, msg.data)
                    except Exception:
                        logger.exception("OneBot 12 消息处理异常")
                elif msg.type == WSMsgType.ERROR:
                    logger.warning("OneBot 12 WS 错误: %s", ws.exception())
                    break
        finally:
            self._clients.pop(ws, None)
            if not self._clients:
                self._connected = False
                logger.info("OneBot 12 全部协议端已断开")
                try:
                    await self.notify_disconnected()
                except Exception:
                    logger.exception("OneBot 12 断连回调异常")
        return ws

    async def _handle_message(self, ws: web.WebSocketResponse, data: str) -> None:
        """分发 WS 文本消息：动作响应（echo 匹配）或事件推送"""
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            logger.warning("OneBot 12 收到非法 JSON 消息: %.200s", data)
            return
        if not isinstance(obj, dict):
            return
        # 动作响应：带 echo 且含 status/retcode（对应我们的 JSON-RPC 请求）
        echo = obj.get("echo")
        if echo and echo in self._pending and ("status" in obj or "retcode" in obj):
            fut = self._pending.pop(echo)
            if not fut.done():
                fut.set_result(obj)
            return
        # 事件推送：type 为 OneBot 12 事件必填字段
        if obj.get("type"):
            await self._on_event(ws, obj)
            return
        logger.debug("OneBot 12 忽略无法识别的消息: %.200s", data)

    async def _on_event(self, ws: web.WebSocketResponse, event: dict) -> None:
        """收到 OneBot 12 事件：更新状态并上报（事件已 v12 格式，直通 Dispatcher）"""
        self._last_heartbeat = time.time()
        self._connected = True
        self_id = str(event.get("self_id") or "")
        if self_id:
            self.self_id = self_id
            self._clients[ws]["self_id"] = self_id
        impl = str(event.get("impl") or "")
        if impl:
            self.impl = impl
            self._clients[ws]["impl"] = impl
        self._clients[ws]["last_active"] = time.time()

        # 覆盖 platform 为适配器名（内部路由语义，与 v11/telegram 一致）
        event = dict(event)
        event["platform"] = self.name

        if event.get("type") == "meta":
            await self.emit_metaevent(event)
        else:
            await self.emit_event(event)

    # ============ 动作调用（JSON-RPC） ============

    def _pick_client(self) -> web.WebSocketResponse | None:
        """选择动作请求目标连接：优先最近活跃的连接"""
        if not self._clients:
            return None
        return max(self._clients, key=lambda ws: self._clients[ws]["last_active"])

    async def _call(
        self, action: str, params: dict | None = None, timeout: float = _ACTION_TIMEOUT
    ) -> dict:
        """发送 OneBot 12 动作请求并等待响应（echo 匹配）"""
        ws = self._pick_client()
        if ws is None:
            raise RuntimeError("OneBot 12 无可用连接（协议端未接入）")
        self._echo_seq += 1
        echo = f"qc{self._echo_seq}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            await ws.send_str(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            self._pending.pop(echo, None)
            raise RuntimeError(f"OneBot 12 动作请求发送失败 ({action}): {exc}") from exc
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(echo, None)
            raise TimeoutError(f"OneBot 12 动作 {action} 响应超时（>{timeout}s）") from None
        if resp.get("status") != "ok" or resp.get("retcode") != 0:
            raise RuntimeError(
                f"OneBot 12 动作 {action} 失败: {resp.get('message') or resp.get('status')} "
                f"(retcode={resp.get('retcode')})"
            )
        return resp.get("data") or {}

    @staticmethod
    def _coerce_message(message: str | list) -> str | list:
        """将消息归一化为 v12 段数组或纯文本（SDK Message 容器转 as_dicts）"""
        if isinstance(message, str):
            return message
        if hasattr(message, "as_dicts"):
            return cast(list, message.as_dicts())
        return message

    async def send_msg(self, message_type: str, target_id: int, message: str | list) -> dict:
        """发送消息（v12 send_message 动作，message 原生 v12 段数组）"""
        detail_type = "group" if message_type == "group" else "private"
        params: dict[str, Any] = {
            "detail_type": detail_type,
            "message": self._coerce_message(message),
        }
        if detail_type == "group":
            params["group_id"] = str(target_id)
        else:
            params["user_id"] = str(target_id)
        return await self._call("send_message", params)

    async def call_api(
        self, action: str, params: dict | None = None, timeout: float = _ACTION_TIMEOUT
    ) -> dict:
        """调用 OneBot 12 标准动作（JSON-RPC 透传）"""
        return await self._call(action, params, timeout)

    def _api_action(self, action: str) -> str:
        """v11 便捷动作名 -> OneBot 12 点分命名空间（group.kick 等）"""
        return {
            "set_group_kick": "group.kick",
            "set_group_ban": "group.ban",
            "set_group_whole_ban": "group.whole_ban",
            "set_group_admin": "group.set_admin",
            "set_group_card": "group.set_card",
            "set_group_name": "group.set_name",
            "get_group_member_list": "group.get_member_list",
            "get_group_member_info": "group.get_member_info",
        }.get(action, action)

    async def approve_request(
        self,
        flag: str,
        approve: bool,
        request_type: str = "friend",
        sub_type: str = "",
    ) -> dict:
        """审批加好友/加群请求（OneBot 12 handle 动作命名空间）"""
        if request_type == "friend":
            return await self._call("friend_request.handle", {"flag": flag, "approve": approve})
        return await self._call("group_request.handle", {"flag": flag, "approve": approve})
