"""平台适配器抽象基类

定义多平台接入的统一契约。平台适配器负责：
1. 建立/维持与平台的连接（WebSocket / 长轮询 / webhook）
2. 将平台事件归一化为 OneBot-11 兼容事件 dict 并上报
3. 将 Bot 的发送请求转换为平台 API 调用

新增平台步骤：
- 继承 PlatformAdapter，实现 start/stop/is_connected/send_msg
- 在 bot/core/platforms/__init__.py 导出
- 在 config.yaml 的 platforms 节注册（enabled + 平台参数）
"""

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("qingci-bot.platforms")


class PlatformAdapter:
    """平台适配器基类（所有平台统一契约）"""

    #: 平台标识（事件 dict 的 platform 字段，如 onebot / telegram）
    name: str = "base"
    #: 展示名（日志/WebUI 使用）
    display_name: str = "Base"

    def __init__(self) -> None:
        self._event_handlers: list[Callable] = []
        self._metaevent_handlers: list[Callable] = []
        self._connect_handlers: list[Callable] = []
        self._disconnect_handlers: list[Callable] = []
        self._reconnect_handlers: list[Callable] = []
        self._api_call_hooks: list[Callable] = []

    # ============ 生命周期（子类实现） ============

    async def start(self) -> None:
        """启动平台连接"""
        raise NotImplementedError

    async def stop(self) -> None:
        """停止平台连接"""
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        """平台连接是否在线"""
        return False

    @property
    def last_heartbeat(self) -> float:
        """最近心跳时间（epoch 秒），无心跳机制返回 0"""
        return 0.0

    def status_info(self) -> dict:
        """扩展平台状态字段（各平台覆盖补充，合并进 get_status 的 platforms 项）"""
        return {}

    # ============ 事件上报（子类调用） ============

    def on_event(self, handler: Callable) -> None:
        """注册事件处理器（事件 dict 上报目标）"""
        if handler not in self._event_handlers:
            self._event_handlers.append(handler)

    def on_metaevent(self, handler: Callable) -> None:
        """注册元事件处理器（heartbeat/lifecycle 等）"""
        if handler not in self._metaevent_handlers:
            self._metaevent_handlers.append(handler)

    def on_connect(self, handler: Callable) -> None:
        """注册连接建立回调"""
        if handler not in self._connect_handlers:
            self._connect_handlers.append(handler)

    def on_disconnect(self, handler: Callable) -> None:
        """注册断连回调"""
        if handler not in self._disconnect_handlers:
            self._disconnect_handlers.append(handler)

    def on_reconnect(self, handler: Callable) -> None:
        """注册重连回调"""
        if handler not in self._reconnect_handlers:
            self._reconnect_handlers.append(handler)

    def on_api_call(self, handler: Callable) -> None:
        """注册平台接口调用钩子（每次 call_api 前触发，可改写参数）"""
        if handler not in self._api_call_hooks:
            self._api_call_hooks.append(handler)

    async def emit_event(self, event: dict) -> None:
        """向 Bot 上报一个事件（子类归一化后调用）

        自动补充 platform 字段；异常隔离，单个处理器异常不影响其余。
        """
        if "platform" not in event:
            event = dict(event)
            event["platform"] = self.name
        for handler in list(self._event_handlers):
            try:
                res = handler(event)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                logger.exception(f"平台事件处理器异常: {self.name}")

    async def emit_metaevent(self, event: dict) -> None:
        """上报元事件（heartbeat/lifecycle）"""
        if "platform" not in event:
            event = dict(event)
            event["platform"] = self.name
        for handler in list(self._metaevent_handlers):
            try:
                res = handler(event)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                logger.exception(f"平台元事件处理器异常: {self.name}")

    async def _trigger(self, handlers: list[Callable]) -> None:
        """触发连接状态回调（异常隔离）"""
        for cb in list(handlers):
            try:
                res = cb()
                if inspect.isawaitable(res):
                    await res
            except Exception:
                logger.exception(f"平台状态回调异常: {self.name}")

    async def notify_connected(self) -> None:
        await self._trigger(self._connect_handlers)

    async def notify_disconnected(self) -> None:
        await self._trigger(self._disconnect_handlers)

    async def notify_reconnected(self) -> None:
        await self._trigger(self._reconnect_handlers)

    # ============ 发送与能力（子类实现） ============

    async def send_msg(self, message_type: str, target_id: int, message: str | list) -> dict:
        """发送消息（平台统一入口）

        OneBot 12 迁移（方案 A）：message 可为文本或 v12 段数组
        （{type, data}，媒体用 file_id）。平台负责将段数组转换为
        自身 API 调用；v11 平台先将段数组序列化为 CQ 码。

        Args:
            message_type: group / private
            target_id: 群号或用户号（Telegram 为 chat_id）
            message: 文本内容或 v12 段数组
        """
        raise NotImplementedError

    async def call_api(self, action: str, params: dict | None = None, timeout: float = 30) -> dict:
        """调用平台能力（透传）

        Args:
            action: 平台 API 名（OneBot action 或 Telegram 方法名）
            params: 参数
            timeout: 超时秒数
        """
        raise NotImplementedError(f"平台 {self.name} 不支持 API: {action}")

    # ============ 工具 ============

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_str(value: Any, default: str = "") -> str:
        return str(value) if value is not None else default


def make_platform(platform_cfg: Any) -> PlatformAdapter | None:
    """按配置创建平台适配器实例

    配置节（config.yaml platforms.<name>）：
        telegram:
          enabled: false
          token: ""
          poll_interval: 1.0

    Returns:
        适配器实例；平台未启用/未知返回 None
    """
    name = getattr(platform_cfg, "name", "") or ""
    enabled = bool(getattr(platform_cfg, "enabled", False))
    if not enabled:
        return None
    if name == "telegram":
        from .telegram import TelegramAdapter

        return TelegramAdapter(
            token=str(getattr(platform_cfg, "token", "") or ""),
            poll_interval=float(getattr(platform_cfg, "poll_interval", 1.0) or 1.0),
            request_timeout=float(getattr(platform_cfg, "request_timeout", 40.0) or 40.0),
            max_retries=int(getattr(platform_cfg, "max_retries", 0) or 0),
        )
    if name == "onebot12":
        from .onebot12 import OneBot12Adapter

        return OneBot12Adapter(
            host=str(getattr(platform_cfg, "host", "127.0.0.1") or "127.0.0.1"),
            port=int(getattr(platform_cfg, "port", 3002) or 3002),
            access_token=str(getattr(platform_cfg, "access_token", "") or ""),
            enabled=enabled,
        )
    logger.warning(f"未知的平台适配器: {name}")
    return None


async def cancel_and_await(task: asyncio.Task | None) -> None:
    """取消并等待后台任务结束（幂等，忽略 CancelledError）"""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
