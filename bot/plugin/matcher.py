"""匹配器系统 — 转发至独立插件 SDK

协议层（Matcher / MatcherContext / 注册工厂）统一由
qingci_plugin_sdk.matcher 维护，主项目不再维护副本，避免两处定义漂移。

注意：SDK 的 MatcherContext 继承 SDK context.MessageContext；主项目
MessageDispatcher 传入的主项目 MessageContext 字段与 SDK 版本完全一致
（见 qingci_plugin_sdk/context.py），from_message_context 按字段复制，兼容。
"""

from qingci_plugin_sdk.matcher import *  # noqa: F401,F403
from qingci_plugin_sdk.matcher import (  # noqa: F401
    Matcher,
    MatcherContext,
    begin_module_collection,
    end_module_collection,
    on_command,
    on_keyword,
    on_message,
    on_notice,
    on_request,
    on_startswith,
)

__all__ = [
    "Matcher",
    "MatcherContext",
    "on_message",
    "on_command",
    "on_startswith",
    "on_keyword",
    "on_notice",
    "on_request",
    "begin_module_collection",
    "end_module_collection",
]
