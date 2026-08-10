"""插件测试工具（bot.testing）验证测试

验证 TestBot 的完整链路：加载插件 → 发送消息 → 断言回复；
以及多轮会话状态、主动发消息、权限控制。
"""

import pytest

from bot.testing import TestBot, group_message, make_notice_event, private_message


@pytest.fixture
def bot():
    return TestBot()


async def test_load_plugin_and_ping(bot):
    ok = await bot.load_plugin("plugin_pkg.echo_plugin")
    assert ok is True

    reply = await bot.send(private_message("/ping"))
    assert reply == "pong"


async def test_send_private_without_load(bot):
    """未加载插件时发送消息应返回 None"""
    reply = await bot.send(private_message("任意内容"))
    assert reply is None


async def test_session_state_multi_turn(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")

    r1 = await bot.send(private_message("/register"))
    assert r1 == "请输入你的名字："

    r2 = await bot.send(private_message("晴", user_id=10001))
    assert r2 == "你好 晴，请输入你的年龄："

    r3 = await bot.send(private_message("18", user_id=10001))
    assert r3 == "注册完成！晴，18岁"

    # 会话状态按 user_id 隔离
    r4 = await bot.send(private_message("/register", user_id=10002))
    assert r4 == "请输入你的名字："


async def test_session_state_isolated_by_user(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")
    await bot.send(private_message("/register", user_id=10001))
    await bot.send(private_message("甲", user_id=10001))

    # 另一用户不共享会话
    r = await bot.send(private_message("/register", user_id=10003))
    assert r == "请输入你的名字："


async def test_group_message(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")

    reply = await bot.send(group_message("/ping", user_id=10001, group_id=20001))
    assert reply == "pong"


async def test_superuser_permission(bot):
    """admin_users 默认含 10001，其他用户无权限"""
    await bot.load_plugin("plugin_pkg.echo_plugin")

    reply_admin = await bot.send(group_message("/notify", user_id=10001, group_id=20001))
    assert reply_admin == "已发送"
    assert bot.sent_messages == [("group", 20001, "管理员通知已发送")]

    reply_normal = await bot.send(group_message("/notify", user_id=99999, group_id=20001))
    assert reply_normal is None


async def test_connection_api_calls(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")
    await bot.send(group_message("/notify", user_id=10001, group_id=20001))
    assert bot.api_calls == []  # 直接 send_* 不经过 call_api


async def test_load_missing_plugin(bot):
    ok = await bot.load_plugin("plugin_pkg.no_such_module")
    assert ok is False


async def test_cleanup(bot):
    await bot.load_plugin("plugin_pkg.echo_plugin")
    assert bot.get_plugin("echo") is not None
    await bot.cleanup()
    assert bot.get_plugin("echo") is None


async def test_make_notice_event_fields():
    event = make_notice_event("group_increase", user_id=10001, group_id=20001)
    assert event["post_type"] == "notice"
    assert event["notice_type"] == "group_increase"
    assert event["user_id"] == 10001
    assert event["group_id"] == 20001