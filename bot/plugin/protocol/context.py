"""消息上下文 — 转发至独立插件 SDK

协议层（MessageContext）统一由 qingci_plugin_sdk.context 维护，
主项目不再维护副本。调度实现见 bot/core/dispatcher.py（MessageDispatcher）。
"""

from qingci_plugin_sdk.context import *  # noqa: F401,F403
from qingci_plugin_sdk.context import MessageContext  # noqa: F401

__all__ = ["MessageContext"]
