"""消息广播机制

允许任意模块注册消息回调，例如 WebSocket 实时推送。
"""

import logging
from typing import Callable, Awaitable

logger = logging.getLogger("qingci-bot.broadcast")

_brokers: list[Callable[[dict], Awaitable[None]]] = []


def register_broker(broker: Callable[[dict], Awaitable[None]]) -> None:
    """注册消息广播回调"""
    _brokers.append(broker)


def unregister_broker(broker: Callable[[dict], Awaitable[None]]) -> None:
    """注销消息广播回调"""
    if broker in _brokers:
        _brokers.remove(broker)


async def broadcast_message(message: dict) -> None:
    """广播消息到所有注册 broker"""
    for broker in list(_brokers):
        try:
            await broker(message)
        except Exception:
            logger.warning("广播消息失败", exc_info=True)