"""OneBot 11 协议层 - 基于 aiocqhttp 的反向 WebSocket 实现

底层使用 aiocqhttp（nonebot 出品）处理 OneBot v11 协议：
- 反向 WebSocket 服务端（端点 /ws、/ws/event、/ws/api）
- 消息段解析、API 调用、access_token 校验
- 事件总线
- 连接状态监控：断连/重连回调 + 自动心跳检测

OneBotConnection 作为外观层，保持与旧 API 兼容：
- on_event(handler) 注册事件处理器
- on_disconnect / on_reconnect / on_connect 注册连接状态回调
- on_metaevent(handler) 注册元事件回调（heartbeat/lifecycle，旁路分发）
- call_api(action, params) 调用 OneBot API
- send_msg / send_group_msg / send_private_msg 便捷方法
- is_connected / last_heartbeat 状态查询

OneBot 实现端（如 LLBot/NapCat）需连接 ws://host:port/ws。
断连时 Qingci-Bot CE Web UI 与 API 保持可用，LLBot 重连后自动恢复消息收发。
"""

import asyncio
import inspect
import logging
import time
from collections.abc import Callable

from aiocqhttp import CQHttp

from .message import segments_to_cq
from .platforms.base import PlatformAdapter
from .v11_compat import v11_event_to_v12

logger = logging.getLogger("qingci-bot.connection")

# 连接状态监控间隔（秒）
_CONNECTION_MONITOR_INTERVAL = 3.0


class OneBotConnection(PlatformAdapter):
    """OneBot 11 反向 WebSocket 连接管理（基于 aiocqhttp）

    实现 PlatformAdapter 契约，作为内置「onebot」平台适配器。
    """

    name = "onebot"
    display_name = "OneBot 11"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3001,
        access_token: str = "",
        enabled: bool = True,
    ):
        self.host = host
        self.port = port
        self.access_token = access_token.strip() if access_token else ""
        self.enabled = enabled

        # aiocqhttp 引擎（反向 WS 模式：不传 api_root）
        self._bot = CQHttp(
            import_name="qingci-bot.connection",
            access_token=self.access_token or None,
            api_timeout_sec=30,
        )
        self._server_task: asyncio.Task | None = None
        self._running = False
        self._event_handlers: list[Callable] = []
        self._last_heartbeat = 0.0

        # 连接状态监控
        self._was_connected = False
        self._monitor_task: asyncio.Task | None = None
        self._on_disconnect_callbacks: list[Callable] = []
        self._on_reconnect_callbacks: list[Callable] = []
        self._on_connect_callbacks: list[Callable] = []
        self._on_metaevent_callbacks: list[Callable] = []
        # 平台接口调用钩子（on_calling_api）：每次 call_api 前触发
        self._api_call_hooks: list[Callable] = []

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
            # 心跳：更新心跳时间并通知元事件回调（不进入事件总线，避免洪峰任务堆积）
            if event.get("meta_event_type") == "heartbeat":
                self._last_heartbeat = time.time()
                logger.debug(f"心跳: {event.get('status', {})}")
                await self._trigger_callbacks(self._on_metaevent_callbacks, event)
                return
            # 生命周期事件（连接建立）
            if event.get("meta_event_type") == "lifecycle":
                logger.info(f"LLBot 生命周期事件: {event.get('sub_type', '')}")
            await self._trigger_callbacks(self._on_metaevent_callbacks, event)
            await self._dispatch_event(event)

    def on_event(self, handler: Callable) -> None:
        """注册事件处理器（兼容旧 API，自动去重避免重复注册）"""
        if handler not in self._event_handlers:
            self._event_handlers.append(handler)

    async def _dispatch_event(self, event):
        """分发事件到所有处理器

        aiocqhttp 的 Event 继承自 dict，可直接当 dict 用。
        OneBot 12 迁移（M3）：v11 事件在适配器内翻译为 v12 事件再上报，
        核心只消费 v12 事件模型（type / detail_type）。
        """
        # Event 是 dict 子类，先转纯 dict 再翻译为 v12 事件
        raw = v11_event_to_v12(dict(event))
        for handler in list(self._event_handlers):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(raw)
                else:
                    handler(raw)
            except Exception:
                logger.exception("事件处理器异常")

    # ============ 连接状态回调 ============

    def on_disconnect(self, handler: Callable) -> None:
        """注册断连回调（async callable，LLBot 断开时触发）"""
        if handler not in self._on_disconnect_callbacks:
            self._on_disconnect_callbacks.append(handler)

    def on_reconnect(self, handler: Callable) -> None:
        """注册重连回调（async callable，LLBot 重新连接时触发）"""
        if handler not in self._on_reconnect_callbacks:
            self._on_reconnect_callbacks.append(handler)

    def on_connect(self, handler: Callable) -> None:
        """注册连接建立回调（async callable，初始连接与重连均触发）

        用于触发插件级 on_bot_connect 生命周期钩子。
        """
        if handler not in self._on_connect_callbacks:
            self._on_connect_callbacks.append(handler)

    def on_metaevent(self, handler: Callable) -> None:
        """注册元事件回调（async callable，heartbeat / lifecycle 等元事件触发）

        元事件不进入事件总线（避免洪峰任务堆积），仅通过本回调旁路分发，
        用于触发插件级 on_metaevent 生命周期钩子。
        """
        if handler not in self._on_metaevent_callbacks:
            self._on_metaevent_callbacks.append(handler)

    def on_api_call(self, handler: Callable) -> None:
        """注册平台接口调用钩子（on_calling_api）

        每次 call_api 前触发。签名：
            async (api_name, params) -> Optional[dict]
        返回新 params 时替换原参数；返回 None 保持原样；抛异常则阻止该次
        API 调用。用于横切鉴权、参数改写、审计。注册自动去重。
        """
        if handler not in self._api_call_hooks:
            self._api_call_hooks.append(handler)

    async def _trigger_api_call_hooks(self, action: str, params: dict) -> dict:
        """执行平台接口调用钩子，返回最终 params（可被钩子改写）

        单个钩子异常隔离（记录后继续下一个）；但钩子主动 raise（鉴权拒绝）
        会上抛，从而阻止该次 API 调用。
        """
        for hook in list(self._api_call_hooks):
            try:
                modified = hook(action, dict(params))
                if inspect.isawaitable(modified):
                    modified = await modified
                if modified is not None:
                    params = modified
            except Exception:
                logger.exception(f"平台接口调用钩子异常: {action}")
                raise
        return params

    async def _trigger_callbacks(self, callbacks: list[Callable], *args) -> None:
        """执行回调列表（异常隔离，支持 async/普通 callable）"""
        for cb in list(callbacks):
            try:
                res = cb(*args)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                logger.exception("连接回调执行异常")

    async def _monitor_connection(self) -> None:
        """后台任务：监控连接状态变化并触发回调"""
        while self._running:
            try:
                connected = self.is_connected
                if not connected and self._was_connected:
                    logger.warning(
                        "LLBot 连接已断开（WebSocket 客户端全部离开），Web UI 与 API 仍可用"
                    )
                    await self._trigger_callbacks(self._on_disconnect_callbacks)
                elif connected and not self._was_connected:
                    logger.info("LLBot 已重新连接，恢复消息收发")
                    await self._trigger_callbacks(self._on_reconnect_callbacks)
                    # 初始连接与重连均触发 on_connect（插件级 on_bot_connect）
                    await self._trigger_callbacks(self._on_connect_callbacks)
                self._was_connected = connected
            except Exception:
                logger.exception("连接状态监控异常")
            await asyncio.sleep(_CONNECTION_MONITOR_INTERVAL)

    # ============ 生命周期 ============

    @staticmethod
    async def _never_shutdown() -> None:
        """永不触发的关闭信号。

        Hypercorn 在未传 shutdown_trigger 时会自行安装 signal 处理器，
        在非主线程（如 desktop 模式后台线程）会抛
        "signal only works in main thread"；显式传入本触发器即可跳过
        signal 安装。服务关闭仍由 stop() 取消 server task 完成。
        """
        await asyncio.get_running_loop().create_future()

    async def start(self) -> None:
        """启动反向 WebSocket 服务器（异步）

        若启动过程中被取消（如 API 层 wait_for 超时），会先回收已创建的
        server task 再重新抛出 CancelledError，避免孤儿 task 占用端口。
        """
        if not self.enabled:
            logger.info(
                f"OneBot 已禁用（onebot.enabled=false），跳过反向 WS 启动: {self.host}:{self.port}"
            )
            return
        self._running = True
        logger.info(f"OneBot WS 服务器启动: ws://{self.host}:{self.port}/ws")
        self._server_task = asyncio.create_task(
            self._bot.run_task(
                host=self.host,
                port=self.port,
                shutdown_trigger=self._never_shutdown,
            )
        )
        try:
            # 最长 2 秒的快速失败轮询：端口占用等绑定失败会很快暴露；
            # 任务持续运行（未 done）即视为已就绪，提前退出
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 2.0
            while loop.time() < deadline:
                if self._server_task.done():
                    exc = self._server_task.exception()
                    if exc:
                        self._running = False
                        raise RuntimeError(f"OneBot WS 服务器启动失败: {exc}")
                await asyncio.sleep(0.02)
                if not self._server_task.done():
                    break  # 任务持续运行即视为已就绪
        except asyncio.CancelledError:
            # 启动被取消：回收已创建的 server task，避免孤儿 task 持续占用端口
            self._running = False
            if self._server_task and not self._server_task.done():
                self._server_task.cancel()
                try:
                    await self._server_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._server_task = None
            logger.warning("OneBot WS 服务器启动被取消，已回收 server task")
            raise
        # 启动连接状态监控
        self._monitor_task = asyncio.create_task(self._monitor_connection())

    async def stop(self) -> None:
        """停止服务器，清理事件处理器"""
        self._running = False
        # 停止连接状态监控
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except (asyncio.CancelledError, Exception):
                pass
        self._monitor_task = None
        # 清理事件处理器，防止重启时重复注册
        self._event_handlers.clear()
        self._on_disconnect_callbacks.clear()
        self._on_reconnect_callbacks.clear()
        self._on_connect_callbacks.clear()
        self._on_metaevent_callbacks.clear()
        # 关闭 Quart server
        try:
            if self._server_task and not self._server_task.done():
                self._server_task.cancel()
                try:
                    await asyncio.wait_for(self._server_task, timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    # 取消信号发出但任务仍在退出（如正在处理关闭回调）：
                    # 继续等待其真正结束，避免遗留 task 触发
                    # "Task destroyed while pending" 警告
                    if self._server_task and not self._server_task.done():
                        await asyncio.gather(self._server_task, return_exceptions=True)
        except Exception:
            logger.exception("停止 OneBot WS 服务器异常")
        self._server_task = None
        logger.info("OneBot WS 服务器已停止")

    # ============ API 调用 ============

    async def call_api(self, action: str, params: dict | None = None, timeout: float = 30) -> dict:
        """调用 OneBot API

        返回 API 响应中的 data 字段（aiocqhttp 已自动解包）。
        调用前触发 on_calling_api 钩子（可改写参数或阻止调用）。
        """
        params = params or {}
        if self._api_call_hooks:
            params = await self._trigger_api_call_hooks(action, params) or {}
        if not isinstance(params, dict):
            params = {}
        try:
            result = await asyncio.wait_for(
                self._bot.call_action(action, **params),
                timeout=timeout,
            )
            return result if isinstance(result, dict) else {"data": result}
        except asyncio.TimeoutError:
            raise TimeoutError(f"API 调用超时: {action}") from None
        except Exception as e:
            logger.error(f"API 调用失败 {action}: {e}")
            raise

    # ============ 便捷 API ============

    async def send_private_msg(self, user_id: int, message: str | list) -> dict:
        """发送私聊消息（message 可为文本 / v11 段数组 / v12 段数组）"""
        return await self.call_api(
            "send_private_msg",
            {
                "user_id": user_id,
                "message": segments_to_cq(message),
            },
        )

    async def send_group_msg(self, group_id: int, message: str | list) -> dict:
        """发送群聊消息（message 可为文本 / v11 段数组 / v12 段数组）"""
        return await self.call_api(
            "send_group_msg",
            {
                "group_id": group_id,
                "message": segments_to_cq(message),
            },
        )

    async def send_msg(self, message_type: str, target_id: int, message: str | list) -> dict:
        """发送消息（message 可为文本 / 段数组，段数组序列化为 CQ 码）"""
        if message_type == "private":
            return await self.send_private_msg(target_id, message)
        elif message_type == "group":
            return await self.send_group_msg(target_id, message)
        else:
            raise ValueError(f"未知的 message_type: {message_type}")

    async def get_group_info(self, group_id: int) -> dict:
        return await self.call_api("get_group_info", {"group_id": group_id})

    async def get_group_member_info(self, group_id: int, user_id: int) -> dict:
        return await self.call_api(
            "get_group_member_info",
            {
                "group_id": group_id,
                "user_id": user_id,
            },
        )

    # ============ 状态 ============

    @property
    def is_connected(self) -> bool:
        """是否有 OneBot 客户端连接（API 或事件通道任一在线即视为已连接）

        aiocqhttp 分别维护 _wsr_api_clients（API 客户端）与
        _wsr_event_clients（事件客户端）；仅连事件通道的实现端
        （如仅上报不调 API）也应视为已连接。
        """
        if not self._running:
            return False
        try:
            # 优先用公开属性，回退到内部属性
            api_clients = getattr(self._bot, "_wsr_api_clients", None)
            if api_clients is None:
                api_clients = getattr(self._bot, "wsr_api_clients", None)
            event_clients = getattr(self._bot, "_wsr_event_clients", None)
            if event_clients is None:
                event_clients = getattr(self._bot, "wsr_event_clients", None)
            return bool(api_clients) or bool(event_clients)
        except Exception:
            return False

    @property
    def last_heartbeat(self) -> float:
        return self._last_heartbeat

    @property
    def bot(self) -> CQHttp:
        """暴露底层 aiocqhttp 实例，供需要高级用法的调用方使用"""
        return self._bot
