"""v11->v12 事件翻译层与 v12 核心链路测试（方案 A 迁移 M3）

覆盖：
- v11_event_to_v12：message / notice（含 group_admin、group_ban 细分）/
  request / meta 事件翻译
- v12 事件经 dispatcher.dispatch 归一化（post_type/message_type 派生）
- TestBot 全链路消费 v12 事件（私聊命令 / 群聊 @ / notice 类型化事件）
"""

import pytest

from bot.core.dispatcher import MessageDispatcher
from bot.core.v11_compat import v11_event_to_v12
from bot.testing import (
    TestBot,
    make_v12_message_event,
    make_v12_notice_event,
    make_v12_request_event,
)

# ============ 翻译层：message ============


def test_v11_message_translated():
    v11 = {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 6283,
        "user_id": 10001,
        "group_id": 20001,
        "self_id": 20002,
        "raw_message": "你好",
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "sender": {"user_id": 10001, "nickname": "A"},
        "platform": "onebot",
    }
    v12 = v11_event_to_v12(v11)
    assert v12["type"] == "message"
    assert v12["detail_type"] == "group"
    assert v12["message_id"] == "6283"
    assert v12["user_id"] == "10001"
    assert v12["group_id"] == "20001"
    assert v12["self_id"] == "20002"
    assert v12["alt_message"] == "你好"
    assert v12["message"] == v11["message"]
    assert v12["platform"] == "onebot"


def test_v11_message_private_translated():
    v12 = v11_event_to_v12({"post_type": "message", "message_type": "private", "user_id": 1})
    assert v12["type"] == "message"
    assert v12["detail_type"] == "private"


# ============ 翻译层：notice ============


def test_v11_notice_group_increase_translated():
    v12 = v11_event_to_v12(
        {
            "post_type": "notice",
            "notice_type": "group_increase",
            "sub_type": "approve",
            "user_id": 10001,
            "group_id": 20001,
            "self_id": 20002,
        }
    )
    assert v12["type"] == "notice"
    assert v12["detail_type"] == "group_member_increase"
    assert v12["user_id"] == "10001"
    assert v12["group_id"] == "20001"


def test_v11_notice_admin_subtype_split():
    assert v11_event_to_v12(
        {"post_type": "notice", "notice_type": "group_admin", "sub_type": "set"}
    )["detail_type"] == "group_admin_set"
    assert v11_event_to_v12(
        {"post_type": "notice", "notice_type": "group_admin", "sub_type": "unset"}
    )["detail_type"] == "group_admin_unset"


def test_v11_notice_ban_subtype_split():
    assert v11_event_to_v12(
        {"post_type": "notice", "notice_type": "group_ban", "sub_type": "ban"}
    )["detail_type"] == "group_member_ban"
    assert v11_event_to_v12(
        {"post_type": "notice", "notice_type": "group_ban", "sub_type": "lift_ban"}
    )["detail_type"] == "group_member_unban"


def test_v11_notice_recall_translated():
    v12 = v11_event_to_v12(
        {"post_type": "notice", "notice_type": "group_recall", "message_id": 88}
    )
    assert v12["detail_type"] == "group_message_delete"
    assert v12["message_id"] == 88  # 携带原始字段供缓冲读取


# ============ 翻译层：request / meta ============


def test_v11_request_translated():
    v12 = v11_event_to_v12(
        {
            "post_type": "request",
            "request_type": "group",
            "sub_type": "add",
            "user_id": 10001,
            "group_id": 20001,
            "flag": "f1",
            "comment": "hi",
        }
    )
    assert v12["type"] == "request"
    assert v12["detail_type"] == "group"
    assert v12["flag"] == "f1"
    assert v12["comment"] == "hi"
    assert v12["user_id"] == "10001"


def test_v11_meta_translated():
    v12 = v11_event_to_v12(
        {"post_type": "meta_event", "meta_event_type": "heartbeat", "status": {"online": True}}
    )
    assert v12["type"] == "meta"
    assert v12["detail_type"] == "heartbeat"
    assert v12["status"] == {"online": True}


def test_v11_unknown_event_passthrough():
    raw = {"foo": "bar"}
    assert v11_event_to_v12(raw) == raw


# ============ v12 事件 -> dispatcher 归一化 ============


def test_v12_event_dispatches_to_context():
    dispatcher = MessageDispatcher()
    ctx = dispatcher.dispatch(
        make_v12_message_event("你好", user_id="10001", detail_type="group", group_id="20001")
    )
    assert ctx.type == "message"
    assert ctx.detail_type == "group"
    assert ctx.post_type == "message"  # 兼容派生
    assert ctx.message_type == "group"  # 兼容派生
    assert ctx.plain_text == "你好"
    assert ctx.user_id == "10001"
    assert ctx.group_id == "20001"


def test_v12_mention_bot_sets_is_at_bot():
    dispatcher = MessageDispatcher()
    ctx = dispatcher.dispatch(
        make_v12_message_event(
            "ping", user_id="10001", detail_type="group", group_id="20001", at_bot=True
        )
    )
    assert ctx.is_at_bot is True
    assert ctx.at_list == ["20002"]


# ============ v12 全链路（TestBot） ============


@pytest.fixture
def bot():
    return TestBot()


async def test_v12_private_command(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")
    reply = await bot.send(make_v12_message_event("/ping", user_id="10001"))
    assert reply == "pong"


async def test_v12_group_command(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")
    reply = await bot.send(
        make_v12_message_event("/ping", user_id="10001", detail_type="group", group_id="20001")
    )
    assert reply == "pong"


async def test_v12_notice_event_typed(bot):
    """v12 notice 事件 → on_notice Matcher → 类型化事件注入"""
    await bot.load_plugin("plugin_pkg.typed_event_plugin")
    ev = make_v12_notice_event(
        "group_member_increase", user_id="10001", group_id="20001", sub_type="approve"
    )
    reply = await bot.send(ev)
    # 插件返回 f"欢迎 {user_id}（由 {operator_id} 操作）入群 {group_id}"
    assert reply == "欢迎 10001（由 0 操作）入群 20001"


async def test_v12_request_event(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")
    ev = make_v12_request_event("group", user_id="10001", group_id="20001", flag="f-1")
    reply = await bot.send(ev)
    assert reply is None or isinstance(reply, bool)  # 无请求 Matcher，不抛异常
