"""类型化事件 — 转发至独立插件 SDK

notice/request 事件模型（NoticeEvent/RequestEvent 及各子类、解析工厂）
统一由 qingci_plugin_sdk.events 维护，主项目不再维护副本。
解析注入实现见 bot/core/dispatcher.py 与 bot/core/di.py。

``__all__`` 必须与 ``qingci_plugin_sdk.events.__all__`` 保持一致：
本文件是手工维护的子集，SDK 新增导出不会自动跟进（星号导入能取到名字，
但 ``from ... import *`` 的调用方拿不到），SDK 升级后需逐项比对补齐。
"""

from qingci_plugin_sdk.events import *  # noqa: F401,F403
from qingci_plugin_sdk.events import (  # noqa: F401
    FriendAddNotice,
    FriendPokeNotice,
    FriendRecallNotice,
    FriendRequestEvent,
    GroupAdminNotice,
    GroupBanNotice,
    GroupCardNotice,
    GroupDecreaseNotice,
    GroupEssenceNotice,
    GroupHonorChangeNotice,
    GroupIncreaseNotice,
    GroupLuckyKingNotice,
    GroupRecallNotice,
    GroupRequestEvent,
    GroupSignInNotice,
    GroupUploadNotice,
    NoticeEvent,
    PokeNotice,
    RequestEvent,
    detail_type_to_notice_type,
    notice_type_to_detail_type,
    parse_event,
    parse_notice_event,
    parse_request_event,
    parse_v12_event,
    translate_v11_event,
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
    "FriendPokeNotice",
    "GroupLuckyKingNotice",
    "GroupHonorChangeNotice",
    "GroupCardNotice",
    "GroupEssenceNotice",
    "GroupSignInNotice",
    "RequestEvent",
    "FriendRequestEvent",
    "GroupRequestEvent",
    "parse_notice_event",
    "parse_request_event",
    "parse_event",
    "parse_v12_event",
    "translate_v11_event",
    "detail_type_to_notice_type",
    "notice_type_to_detail_type",
]
