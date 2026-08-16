"""类型化事件功能测试

验证 notice/request 事件在 Dispatcher 中被解析为类型化对象，
handler 通过参数注解注入：
- 子类注解（GroupIncreaseNotice/GroupBanNotice/GroupRequestEvent 等）
- 字段类型化（int 安全转换、字符串字段）
- 未知 notice_type 回退基类
- 与旧 dict 用法兼容（ctx.raw_event 仍可用）
"""

import pytest

from bot.plugin.events import (
    FriendRequestEvent,
    GroupIncreaseNotice,
    GroupRequestEvent,
    NoticeEvent,
    RequestEvent,
    parse_notice_event,
    parse_request_event,
)
from bot.testing import TestBot, make_notice_event, make_request_event


@pytest.fixture
def bot():
    return TestBot()


async def test_notice_typed_subclass(bot):
    """群成员增加：handler 收到类型化子类"""
    await bot.load_plugin("plugin_pkg.typed_event_plugin")

    reply = await bot.send(
        make_notice_event(
            "group_increase",
            group_id=20001,
            user_id=30001,
            operator_id=40001,
            sub_type="invite",
        )
    )
    assert reply == "欢迎 30001（由 40001 操作）入群 20001"


async def test_notice_fields_typed(bot):
    """群禁言：duration 字段 int 类型化"""
    await bot.load_plugin("plugin_pkg.typed_event_plugin")

    reply = await bot.send(
        make_notice_event(
            "group_ban",
            group_id=20001,
            user_id=30002,
            operator_id=40001,
            duration=3600,
            sub_type="ban",
        )
    )
    assert reply == "禁言 30002 3600 秒（ban）"


async def test_request_group_approval(bot):
    """加群请求：类型化字段可用，返回审批结果"""
    await bot.load_plugin("plugin_pkg.typed_event_plugin")

    # add 子类型 -> 同意
    reply = await bot.send(
        make_request_event("group", group_id=20001, user_id=30003, sub_type="add")
    )
    assert reply is True

    # invite 子类型 -> 拒绝
    reply2 = await bot.send(
        make_request_event("group", group_id=20001, user_id=30004, sub_type="invite")
    )
    assert reply2 is False


async def test_request_friend_comment(bot):
    """加好友请求：comment 字段类型化"""
    await bot.load_plugin("plugin_pkg.typed_event_plugin")

    reply = await bot.send(make_request_event("friend", user_id=30005, comment="你好"))
    assert reply is True

    reply2 = await bot.send(make_request_event("friend", user_id=30006, comment="拒绝"))
    assert reply2 is False


async def test_parse_notice_unknown_type_fallback(bot):
    """未知 notice_type 回退基类，通用字段仍类型化"""
    evt = parse_notice_event(
        {"post_type": "notice", "notice_type": "unknown_thing", "user_id": "30007", "group_id": 1}
    )
    assert isinstance(evt, NoticeEvent)
    assert not isinstance(evt, GroupIncreaseNotice)
    assert evt.user_id == 30007  # 字符串安全转 int


async def test_parse_notice_safe_int():
    """非法数值安全回退默认值"""
    evt = parse_notice_event(
        {"post_type": "notice", "notice_type": "group_increase", "user_id": "abc", "group_id": None}
    )
    assert evt.user_id == 0
    assert evt.group_id == 0


async def test_parse_request_group():
    """加群请求解析为 GroupRequestEvent"""
    evt = parse_request_event(
        {"post_type": "request", "request_type": "group", "group_id": 20001, "user_id": 30008}
    )
    assert isinstance(evt, GroupRequestEvent)
    assert evt.group_id == 20001


async def test_parse_request_friend():
    """加好友请求解析为 FriendRequestEvent"""
    evt = parse_request_event(
        {"post_type": "request", "request_type": "friend", "user_id": 30009, "comment": "hi"}
    )
    assert isinstance(evt, FriendRequestEvent)
    assert evt.comment == "hi"


async def test_legacy_raw_event_still_works(bot):
    """旧用法兼容：ctx.raw_event dict 仍可用"""
    await bot.load_plugin("plugin_pkg.typed_event_plugin")

    event = make_notice_event("group_ban", group_id=20001, user_id=30010, duration=60)
    await bot.send(event)
    # 直接验证解析器：typed event 保留 raw_event
    evt = parse_notice_event(event)
    assert evt.raw_event["notice_type"] == "group_ban"
    assert evt.raw_event["duration"] == 60


async def test_request_event_base_class():
    """RequestEvent 基类可作注解类型"""
    evt = parse_request_event({"post_type": "request", "request_type": "friend"})
    assert isinstance(evt, RequestEvent)
