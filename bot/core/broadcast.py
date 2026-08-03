"""消息广播机制

允许任意模块注册消息回调，例如 WebSocket 实时推送。
"""

import asyncio
import logging
from typing import Callable, Awaitable

logger = logging.getLogger("qingci-bot.broadcast")

_brokers: list[Callable[[dict], Awaitable[None]]] = []


def register_broker(broker: Callable[[dict], Awaitable[None]]) -> None:
    """注册消息广播回调（自动去重）"""
    if broker not in _brokers:
        _brokers.append(broker)


def unregister_broker(broker: Callable[[dict], Awaitable[None]]) -> None:
    """注销消息广播回调"""
    if broker in _brokers:
        _brokers.remove(broker)


async def broadcast_message(message: dict) -> None:
    """广播消息到所有注册 broker（并发执行）"""
    if not _brokers:
        return
    await asyncio.gather(
        *[broker(message) for broker in _brokers],
        return_exceptions=True,
    )