"""多平台适配器测试

验证：
- Telegram Update 归一化为 OneBot-11 兼容事件 dict（私聊/群聊）
- 未知 chat 类型忽略、文本提取（text/caption）
- send_msg / call_api 映射（sendMessage / OneBot action 别名）
- emit_event 自动注入 platform 字段
- Bot 回复按 ctx.platform 路由到对应适配器
- dispatcher 将 platform 透传到 MessageContext
- make_platform 配置解析（禁用/未知平台返回 None）
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot.core.dispatcher import MessageDispatcher
from bot.core.platforms.base import make_platform
from bot.core.platforms.telegram import TelegramAdapter


@pytest.fixture
def adapter() -> TelegramAdapter:
    return TelegramAdapter(token="test:token")


def _message(**kw) -> dict:
    base = {
        "message_id": 42,
        "date": 1786889000,
        "from": {"id": 10001, "first_name": "Alice", "username": "alice"},
        "chat": {"id": 20001, "type": "private", "first_name": "Alice"},
        "text": "你好",
    }
    base.update(kw)
    return base


# ---------- 归一化 ----------


def test_normalize_private_message(adapter):
    event = adapter._normalize_message(_message())
    assert event is not None
    assert event["post_type"] == "message"
    assert event["message_type"] == "private"
    assert event["user_id"] == 10001
    assert event["group_id"] == 0
    assert event["message_id"] == "42"
    assert event["raw_message"] == "你好"
    assert event["plain_text"] == "你好"
    assert event["message"] == [{"type": "text", "data": {"text": "你好"}}]
    assert event["sender"]["nickname"] == "Alice"
    assert event["platform"] == "telegram"


def test_normalize_group_message(adapter):
    msg = _message(
        chat={"id": 20002, "type": "group", "title": "测试群"},
        text="群消息",
    )
    event = adapter._normalize_message(msg)
    assert event["message_type"] == "group"
    assert event["group_id"] == 20002
    assert event["user_id"] == 10001


def test_normalize_caption_fallback(adapter):
    """图片消息无 text 时用 caption"""
    msg = _message(text=None, caption="图片说明")
    event = adapter._normalize_message(msg)
    assert event["raw_message"] == "图片说明"


def test_normalize_ignores_unknown_chat(adapter):
    """非 private/group/supergroup 类型（如 channel）忽略"""
    msg = _message(chat={"id": 30001, "type": "channel", "title": "频道"})
    assert adapter._normalize_message(msg) is None


def test_emit_event_adds_platform(adapter):
    got = []

    async def handler(event):
        got.append(event)

    adapter.on_event(handler)
    asyncio.run(adapter.emit_event({"post_type": "message", "user_id": 1}))
    assert got[0]["platform"] == "telegram"


# ---------- 发送与能力 ----------


async def test_send_msg_maps_to_send_message(adapter, monkeypatch):
    calls = {}

    async def fake_api(method, **params):
        calls["method"] = method
        calls["params"] = params
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg("private", 20001, "hi")
    assert calls["method"] == "sendMessage"
    assert calls["params"]["chat_id"] == 20001
    assert calls["params"]["text"] == "hi"


async def test_call_api_maps_onebot_actions(adapter, monkeypatch):
    calls = {}

    async def fake_api(method, **params):
        calls["method"] = method
        calls["params"] = params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.call_api("send_private_msg", {"user_id": 1, "message": "x"})
    assert calls["method"] == "sendMessage"
    assert calls["params"]["chat_id"] == 1
    assert calls["params"]["text"] == "x"

    await adapter.call_api("send_group_msg", {"group_id": 2, "message": "y"})
    assert calls["params"]["chat_id"] == 2


async def test_call_api_passthrough(adapter, monkeypatch):
    calls = {}

    async def fake_api(method, **params):
        calls["method"] = method
        calls["params"] = params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.call_api("getChatMemberCount", {"chat_id": 1})
    assert calls["method"] == "getChatMemberCount"


# ---------- 配置解析 ----------


def test_make_platform_disabled_returns_none():
    assert make_platform(SimpleNamespace(name="telegram", enabled=False)) is None
    assert make_platform(SimpleNamespace(name="unknown", enabled=True)) is None


def test_make_platform_creates_telegram():
    inst = make_platform(SimpleNamespace(name="telegram", enabled=True, token="t", poll_interval=2.0))
    assert inst is not None
    assert inst.name == "telegram"
    assert inst.token == "t"


# ---------- Bot 平台路由 ----------


class FakeTelegram(TelegramAdapter):
    """测试用：替换 _api 记录发送，start/stop 幂等"""

    def __init__(self):
        super().__init__(token="test:token")
        self.sent: list[tuple] = []
        self.started = False

    async def _api(self, method: str, **params):
        if method == "getMe":
            return {"id": 12345, "username": "testbot"}
        if method == "sendMessage":
            self.sent.append((params.get("chat_id"), params.get("text")))
            return {"message_id": 1}
        return {}

    async def start(self):
        self.started = True
        self._running = True

    async def stop(self):
        self.started = False
        self._running = False


async def test_bot_reply_routes_to_platform(bot):
    """Telegram 事件 → 回复经 Telegram 适配器发送"""
    from qingci_plugin_sdk import PluginBase, on_command

    tg = FakeTelegram()
    bot.platforms["telegram"] = tg

    class EchoPlugin(PluginBase):
        name = "echo"
        version = "1.0.0"

        async def on_load(self):
            pass

        async def on_unload(self):
            pass

    async def ping_handler(ctx):
        return "pong"

    p = EchoPlugin()
    p.matchers = [on_command("ping")(ping_handler)]
    _register(bot, p)

    telegram_event = {
        "post_type": "message",
        "message_type": "private",
        "message_id": "99",
        "user_id": 10001,
        "group_id": 0,
        "self_id": 12345,
        "raw_message": "/ping",
        "message": [{"type": "text", "data": {"text": "/ping"}}],
        "plain_text": "/ping",
        "platform": "telegram",
    }
    reply = await bot.send(telegram_event)
    assert reply == "pong"
    # 显式走发送链路：按 ctx.platform 路由到 Telegram 适配器
    ctx = bot.dispatcher.dispatch(telegram_event)
    assert ctx.platform == "telegram"
    await bot._send_reply(ctx, reply)
    assert tg.sent == [(10001, "pong")]


def _register(bot, plugin):
    """将测试插件注册进 bot 的 PluginManager（模拟 _init_plugin 后的状态）"""
    from bot.plugin import PluginStatus

    plugin._status = PluginStatus.LOADED
    for m in plugin.matchers or []:
        if not m.owner:
            m.owner = plugin.name
    bot.plugin_manager._plugins[plugin.name] = plugin
    bot.plugin_manager._invalidate_matchers_cache()
    return plugin


async def test_onebot_event_still_uses_connection(bot):
    """OneBot 事件回复仍走主 connection"""
    from qingci_plugin_sdk import PluginBase, on_command

    tg = FakeTelegram()
    bot.platforms["telegram"] = tg

    onebot_event = {
        "post_type": "message",
        "message_type": "private",
        "message_id": "1",
        "user_id": 10002,
        "self_id": 123,
        "raw_message": "/hi",
        "message": [{"type": "text", "data": {"text": "/hi"}}],
        "platform": "onebot",
    }

    class HelloPlugin(PluginBase):
        name = "hello2"
        version = "1.0.0"

        async def on_load(self):
            pass

        async def on_unload(self):
            pass

    async def hi_handler(ctx):
        return "hello"

    p = HelloPlugin()
    p.matchers = [on_command("hi")(hi_handler)]
    _register(bot, p)
    await bot.send(onebot_event)
    # 显式走发送链路：onebot 事件路由回主 connection
    ctx = bot.dispatcher.dispatch(onebot_event)
    await bot._send_reply(ctx, "hello")
    assert tg.sent == []  # telegram 未收到
    assert bot.connection.sent == [("private", 10002, "hello")]  # 主连接收到


def test_dispatcher_platform_field():
    """dispatch 透传 platform 到 MessageContext"""
    disp = MessageDispatcher()
    ctx = disp.dispatch(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": "1",
            "user_id": 1,
            "self_id": 2,
            "raw_message": "x",
            "message": [{"type": "text", "data": {"text": "x"}}],
            "platform": "telegram",
        }
    )
    assert ctx.platform == "telegram"
    # 默认 onebot
    ctx2 = disp.dispatch({"post_type": "message", "message_type": "private", "message_id": "2", "user_id": 1, "self_id": 2})
    assert ctx2.platform == "onebot"


def test_get_status_platforms(bot):
    """get_status 返回各平台状态（名称/连接/心跳/self_id）"""
    tg = FakeTelegram()
    tg.self_id = 12345
    bot.platforms["telegram"] = tg
    bot._running = True

    status = bot.get_status()
    platforms = {p["name"]: p for p in status["platforms"]}
    assert set(platforms.keys()) == {"onebot", "telegram"}
    assert platforms["onebot"]["display_name"] == "OneBot 11"
    assert platforms["telegram"]["display_name"] == "Telegram"
    assert platforms["telegram"]["self_id"] == 12345
    assert platforms["telegram"]["connected"] is False  # 未启动
