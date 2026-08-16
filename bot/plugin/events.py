"""类型化事件 — 转发至独立插件 SDK

notice/request 事件模型（NoticeEvent/RequestEvent 及各子类、解析工厂）
统一由 qingci_plugin_sdk.events 维护，主项目不再维护副本。
解析注入实现见 bot/core/dispatcher.py 与 bot/core/di.py。
"""

from qingci_plugin_sdk.events import *  # noqa: F401,F403
from qingci_plugin_sdk.events import (  # noqa: F401
    FriendAddNotice,
    FriendRecallNotice,
    FriendRequestEvent,
    GroupAdminNotice,
    GroupBanNotice,
    GroupDecreaseNotice,
    GroupIncreaseNotice,
    GroupRecallNotice,
    GroupRequestEvent,
    GroupUploadNotice,
    NoticeEvent,
    PokeNotice,
    RequestEvent,
    parse_event,
    parse_notice_event,
    parse_request_event,
)

__all__ = [
    "NoticeEvent",
    "GroupIncreaseNotice",
    "GroupDecreaseNotice",
    "GroupBanNotice",
    "GroupAdminNotice",
    "GroupRecallNotice",
    "FriendRecallNotice",
    "FriendAddNotice",
    "GroupUploadNotice",
    "PokeNotice",
    "RequestEvent",
    "FriendRequestEvent",
    "GroupRequestEvent",
    "parse_event",
    "parse_notice_event",
    "parse_request_event",
]
