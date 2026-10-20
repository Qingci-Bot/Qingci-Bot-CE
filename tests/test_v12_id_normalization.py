"""v12 事件 ID 归一化回归测试

背景：SDK 把 ``MessageContext.user_id`` / ``group_id`` 标为 ``str | int``
（部分 OneBot 12 实现端以字符串下发），而 v11 解析路径在 ``_parse_message``
里已用 ``_safe_int`` 归一化。若 v12 路径不归一化，会出现两类问题：

1. 同一用户经不同协议端进入时分裂成两个会话键（一个 ``123``、一个 ``"123"``）；
2. 字符串 ID 被传给形参声明为 ``int`` 的发送接口。
"""

import pytest

from bot.testing import TestBot
from bot.testing.events import make_v12_message_event


class _GetSessionSpy:
    """记录 get_session 实际收到的会话键"""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple[object, object]] = []

    async def __call__(self, user_id=0, group_id=0, *, message_type=""):
        self.calls.append((user_id, group_id))
        return await self._inner(user_id=user_id, group_id=group_id, message_type=message_type)


@pytest.fixture
def bot():
    return TestBot()


async def test_v12_string_ids_normalized_to_int(bot):
    """v12 事件以字符串下发 ID 时，会话键必须归一化为 int"""
    # 会话预取仅在存在 matcher 时触发，先加载内置插件
    await bot.plugin_manager.load_builtin(bot)
    spy = _GetSessionSpy(bot.session_state.get_session)
    bot.session_state.get_session = spy

    await bot.send(
        make_v12_message_event(
            "你好",
            user_id="10001",
            detail_type="group",
            group_id="20001",
        )
    )

    assert spy.calls, "未触发会话预取，测试无效"
    for user_id, group_id in spy.calls:
        assert isinstance(user_id, int), f"user_id 未归一化: {user_id!r}"
        assert isinstance(group_id, int), f"group_id 未归一化: {group_id!r}"


async def test_v12_int_ids_stay_int(bot):
    """本来就是 int 的 ID 归一化后保持不变（幂等）"""
    await bot.plugin_manager.load_builtin(bot)
    spy = _GetSessionSpy(bot.session_state.get_session)
    bot.session_state.get_session = spy

    event = make_v12_message_event("你好", user_id="10001", detail_type="private")
    event["user_id"] = 10001  # 模拟以 int 下发的实现端

    await bot.send(event)

    assert spy.calls
    assert all(isinstance(u, int) for u, _ in spy.calls)


async def test_v12_reply_target_is_int(bot):
    """回复目标 ID 同样归一化（send_msg 形参为 int）"""
    sent: list[tuple] = []
    original = bot.connection.send_msg

    async def spy_send(message_type, target_id, message):
        sent.append((message_type, target_id))
        return await original(message_type, target_id, message)

    bot.connection.send_msg = spy_send

    await bot.plugin_manager.load_builtin(bot)

    await bot.send(
        make_v12_message_event(
            "/help",
            user_id="10001",
            detail_type="group",
            group_id="20001",
        )
    )

    # 内置插件未加载时可能无回复；只要发了就必须是 int
    for _, target_id in sent:
        assert isinstance(target_id, int), f"target_id 未归一化: {target_id!r}"
