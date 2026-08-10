"""插件测试工具包

为插件作者提供 pytest 可用的轻量测试环境：
- TestBot：不启动真实 Bot 的测试沙箱（加载插件 / 模拟事件 / 断言回复）
- 事件构造器：私聊/群聊消息、@、图片、notice、request 事件

用法：
    import pytest
    from bot.testing import TestBot, private_message

    @pytest.fixture
    def bot():
        return TestBot()

    async def test_ping(bot):
        await bot.load_plugin("my_plugin")
        reply = await bot.send(private_message("/ping"))
        assert reply == "pong"
"""

from .bot import FakeConfig, FakeConnection, TestBot
from .events import (
    make_message_event,
    make_notice_event,
    make_request_event,
    private_message,
    group_message,
)

__all__ = [
    "TestBot",
    "FakeConfig",
    "FakeConnection",
    "make_message_event",
    "make_notice_event",
    "make_request_event",
    "private_message",
    "group_message",
]