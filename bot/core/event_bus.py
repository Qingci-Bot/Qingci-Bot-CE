"""事件总线 — 跨插件发布-订阅事件广播

用于插件间解耦协作：插件 A 发布事件，关注该事件的插件接收并响应，
无需显式依赖调用（区别于 exports 服务式调用）。

特性：
- subscribe(event_type, handler) 订阅指定事件；订阅 "*" 接收所有事件
- publish(event_type, **data) 发布事件，异步通知所有订阅者（异常隔离）
- 支持 sync / async handler
- 线程安全：publish/subscribe 可在任意上下文调用
"""

import asyncio
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger("qingci-bot.event_bus")

# 通配事件类型：订阅 "*" 的 handler 接收所有事件
WILDCARD = "*"


class EventBus:
    """轻量级发布-订阅事件总线"""

    def __init__(self):
        # event_type -> set[handler]
        self._subscribers: dict[str, set[Callable]] = {}
        self._lock = asyncio.Lock()  # 异步路径锁（publish/unsubscribe）
        self._thread_lock = threading.Lock()  # 同步路径锁（subscribe_sync）

    # ---- 订阅 ----

    async def subscribe(self, event_type: str, handler: Callable) -> None:
        """订阅事件。handler 可为 sync/async callable，接收 (event_type, data)

        Args:
            event_type: 事件类型，订阅 "*" 接收所有事件
            handler: async (event_type: str, data: dict) -> None
        """
        if not event_type:
            raise ValueError("事件类型不能为空")
        async with self._lock:
            self._subscribers.setdefault(event_type, set()).add(handler)

    def subscribe_sync(self, event_type: str, handler: Callable) -> None:
        """同步订阅（兼容非 async 上下文；线程锁保护订阅集合）"""
        if not event_type:
            raise ValueError("事件类型不能为空")
        with self._thread_lock:
            self._subscribers.setdefault(event_type, set()).add(handler)

    async def unsubscribe(self, event_type: str, handler: Callable) -> bool:
        """取消订阅，返回是否成功"""
        async with self._lock:
            subs = self._subscribers.get(event_type)
            if subs and handler in subs:
                subs.discard(handler)
                if not subs:
                    self._subscribers.pop(event_type, None)
                return True
            return False

    # ---- 发布 ----

    async def publish(self, event_type: str, **data) -> None:
        """发布事件，异步通知所有订阅者

        异常隔离：单个订阅者异常不影响其他订阅者。
        """
        if not event_type:
            logger.warning("publish 调用忽略空事件类型")
            return
        handlers = set(self._subscribers.get(event_type, ()))
        # 通配订阅者也接收
        handlers.update(self._subscribers.get(WILDCARD, ()))
        for handler in handlers:
            try:
                res = handler(event_type, data)
                if hasattr(res, "__await__"):
                    await res
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    f"事件 {event_type} 订阅者执行异常: {getattr(handler, '__name__', handler)!r}"
                )

    # ---- 查询 ----

    def has_subscribers(self, event_type: str) -> bool:
        """是否有订阅者（含通配）"""
        return bool(self._subscribers.get(event_type)) or bool(self._subscribers.get(WILDCARD))

    def subscriber_count(self, event_type: str) -> int:
        """订阅者数量（不含通配）"""
        return len(self._subscribers.get(event_type, ()))

    async def clear(self) -> None:
        """清空所有订阅"""
        async with self._lock:
            self._subscribers.clear()
