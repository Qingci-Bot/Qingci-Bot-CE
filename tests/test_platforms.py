"""多平台适配器测试

验证：
- Telegram Update 归一化为 OneBot-12 兼容事件 dict（私聊/群聊）
- 未知 chat 类型忽略、文本提取（text/caption）
- send_msg / call_api 映射（sendMessage / OneBot action 别名）
- emit_event 自动注入 platform 字段
- Bot 回复按 ctx.platform 路由到对应适配器
- dispatcher 将 platform 透传到 MessageContext
- make_platform 配置解析（禁用/未知平台返回 None）
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from bot.core.dispatcher import MessageDispatcher
from bot.core.platforms.base import make_platform
from bot.core.platforms.telegram import (
    _BACKOFF_MAX,
    _BACKOFF_MIN,
    TelegramAdapter,
    TelegramAPIError,
    TelegramNotFoundError,
)


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
    assert event["type"] == "message"
    assert event["detail_type"] == "private"
    assert event["user_id"] == "10001"
    assert event["group_id"] == ""
    assert event["message_id"] == "42"
    assert event["alt_message"] == "你好"
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
    assert event["detail_type"] == "group"
    assert event["group_id"] == "20002"
    assert event["user_id"] == "10001"


def test_normalize_caption_fallback(adapter):
    """图片消息无 text 时用 caption"""
    msg = _message(text=None, caption="图片说明")
    event = adapter._normalize_message(msg)
    assert event["alt_message"] == "图片说明"


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
    inst = make_platform(
        SimpleNamespace(name="telegram", enabled=True, token="t", poll_interval=2.0)
    )
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


async def test_v12_telegram_event_dispatch(bot):
    """Telegram v12 事件（type/detail_type）经 v12 归一化后正常触发回复"""
    from qingci_plugin_sdk import PluginBase, on_command

    tg = FakeTelegram()
    bot.platforms["telegram"] = tg

    class V12Plugin(PluginBase):
        name = "v12echo"
        version = "1.0.0"

        async def on_load(self):
            pass

        async def on_unload(self):
            pass

    async def ping_handler(ctx):
        return "pong-v12"

    p = V12Plugin()
    p.matchers = [on_command("ping")(ping_handler)]
    _register(bot, p)

    # 与 TelegramAdapter._normalize_message 产出一致的 v12 事件
    v12_event = {
        "type": "message",
        "detail_type": "private",
        "sub_type": "friend",
        "id": "99",
        "impl": "telegram",
        "platform": "telegram",
        "self_id": "12345",
        "message_id": "99",
        "message": [{"type": "text", "data": {"text": "/ping"}}],
        "alt_message": "/ping",
        "user_id": "10001",
        "group_id": "",
    }
    reply = await bot.send(v12_event)
    assert reply == "pong-v12"
    ctx = bot.dispatcher.dispatch(v12_event)
    assert ctx.platform == "telegram"
    assert ctx.message_type == "private"  # 由 detail_type 派生
    assert ctx.message_id == "99"
    await bot._send_reply(ctx, reply)
    assert tg.sent == [(10001, "pong-v12")]  # 回复路由回 Telegram 适配器


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
    ctx2 = disp.dispatch(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": "2",
            "user_id": 1,
            "self_id": 2,
        }
    )
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


# ---------- Telegram 增强：@提及 ----------


def test_normalize_group_mention_bot(adapter):
    """群聊 @Bot（mention entity）命中 → mention 段 + is_at_bot=True"""
    adapter.self_id = 12345
    adapter.username = "MyBot"
    msg = _message(
        chat={"id": 20002, "type": "group", "title": "测试群"},
        text="hi @MyBot repl",
        entities=[{"type": "mention", "offset": 3, "length": 7}],
    )
    event = adapter._normalize_message(msg)
    at_segs = [s for s in event["message"] if s["type"] == "mention"]
    assert event["is_at_bot"] is True
    assert any(s["data"]["user_id"] == "12345" for s in at_segs)


def test_normalize_group_mention_other(adapter):
    """群聊 @其他用户 → 不命中 Bot，无 mention 段，is_at_bot=False"""
    adapter.self_id = 12345
    adapter.username = "MyBot"
    msg = _message(
        chat={"id": 20002, "type": "group", "title": "测试群"},
        text="@Alice hi",
        entities=[{"type": "mention", "offset": 0, "length": 7}],
    )
    event = adapter._normalize_message(msg)
    assert event["is_at_bot"] is False
    assert not [s for s in event["message"] if s["type"] == "mention"]
    assert event["at_list"]  # 其他用户提及仍记录到 at_list


def test_normalize_group_text_mention_bot(adapter):
    """text_mention entity 命中 Bot（无 @用户名文本）→ is_at_bot=True"""
    adapter.self_id = 12345
    msg = _message(
        chat={"id": 20002, "type": "group", "title": "测试群"},
        text="hi",
        entities=[{"type": "text_mention", "offset": 0, "length": 2, "user": {"id": 12345}}],
    )
    event = adapter._normalize_message(msg)
    assert event["is_at_bot"] is True
    assert any(s["data"]["user_id"] == "12345" for s in event["message"] if s["type"] == "mention")


def test_normalize_private_no_at_segment(adapter):
    """私聊消息不注入 mention 段（触发规则天然放行 private）"""
    adapter.self_id = 12345
    event = adapter._normalize_message(_message())
    assert event["is_at_bot"] is True
    assert not [s for s in event["message"] if s["type"] == "mention"]
    assert event["message"] == [{"type": "text", "data": {"text": "你好"}}]


# ---------- Telegram 增强：图片 ----------


def test_normalize_photo_adds_image_segment(adapter):
    """photo（取末项最大）→ image 段 + images"""
    adapter.self_id = 12345
    msg = _message(
        chat={"id": 20002, "type": "group", "title": "测试群"},
        text="看",
        photo=[
            {"file_id": "P1", "width": 100, "height": 100},
            {"file_id": "P2", "width": 800, "height": 800},
        ],
    )
    event = adapter._normalize_message(msg)
    assert event["images"] == ["P2"]
    imgs = [s for s in event["message"] if s["type"] == "image"]
    assert imgs and imgs[0]["data"]["file_id"] == "P2"


def test_normalize_image_document(adapter):
    """image/* document → image 段"""
    adapter.self_id = 12345
    msg = _message(
        chat={"id": 20002, "type": "group", "title": "测试群"},
        document={"file_id": "D1", "mime_type": "image/png", "file_name": "a.png"},
    )
    event = adapter._normalize_message(msg)
    assert event["images"] == ["D1"]


# ---------- Telegram 增强：发送 ----------


async def test_send_msg_image_url_routes_photo(adapter, monkeypatch):
    """send_msg 含 image 段（URL）→ sendPhoto（photo 参数 + caption）"""
    calls = {}

    async def fake_api(method, **params):
        calls["method"], calls["params"] = method, params
        return {"ok": True, "result": {}}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg(
        "group",
        20002,
        [
            {"type": "text", "data": {"text": "看图"}},
            {"type": "image", "data": {"file_id": "https://x/a.png"}},
        ],
    )
    assert calls["method"] == "sendPhoto"
    assert calls["params"]["photo"] == "https://x/a.png"
    assert calls["params"]["chat_id"] == 20002
    assert calls["params"]["caption"] == "看图"


async def test_send_msg_image_file_id(adapter, monkeypatch):
    """非 URL/本地路径的引用按 Telegram file_id 传给 photo 参数"""
    calls = {}

    async def fake_api(method, **params):
        calls["params"] = params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg("private", 10001, [{"type": "image", "data": {"file_id": "AgACtgID"}}])
    assert calls["params"]["photo"] == "AgACtgID"


async def test_send_msg_base64_uploads(adapter, monkeypatch):
    """base64:// 引用 → multipart 上传（files 带文件）"""
    calls = {}

    async def fake_api(method, **params):
        calls["params"] = params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg(
        "private", 10001, [{"type": "image", "data": {"file_id": "base64://aGVsbG8="}}]
    )
    files = calls["params"]["files"]
    assert files  # multipart
    assert files["photo"][1] == b"hello"


async def test_call_api_group_msg_image(adapter, monkeypatch):
    """call_api send_group_msg 含图片 → sendPhoto"""
    calls = {}

    async def fake_api(method, **params):
        calls["method"] = method
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.call_api(
        "send_group_msg",
        {
            "group_id": 20002,
            "message": [{"type": "image", "data": {"file_id": "http://a/b.jpg"}}],
        },
    )
    assert calls["method"] == "sendPhoto"


def test_parse_v12_segments_media_text_reply(adapter):
    """v12 段解析：提取媒体段、渲染 mention、回传 reply_id"""
    media, text, reply = TelegramAdapter._parse_v12_segments(
        [
            {"type": "text", "data": {"text": "hi "}},
            {"type": "mention", "data": {"user_id": "10001"}},
            {"type": "image", "data": {"file_id": "f"}},
        ]
    )
    assert media == [{"type": "image", "file": "f"}]
    assert text == "hi @10001"
    assert reply == 0

    media, text, reply = TelegramAdapter._parse_v12_segments(
        [
            {"type": "reply", "data": {"message_id": "42"}},
            {"type": "voice", "data": {"file_id": "v1"}},
            {"type": "video", "data": {"file_id": "vd1"}},
            {"type": "text", "data": {"text": "说明"}},
        ]
    )
    assert media == [{"type": "voice", "file": "v1"}, {"type": "video", "file": "vd1"}]
    assert text == "说明"
    assert reply == 42


async def test_send_msg_text_string_still_works(adapter, monkeypatch):
    """纯文本字符串仍按文本发送（兼容旧调用方）"""
    calls = {}

    async def fake_api(method, **params):
        calls["method"], calls["params"] = method, params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg("private", 10001, "hi")
    assert calls["method"] == "sendMessage"
    assert calls["params"]["text"] == "hi"


def test_normalize_voice_and_video(adapter):
    """语音 → voice 段，视频 → video 段（v12 段）"""
    adapter.self_id = 12345
    msg = _message(
        chat={"id": -10020002, "type": "group", "title": "测试群"},
        text="",
        voice={"file_id": "VOICE1", "duration": 5},
    )
    event = adapter._normalize_message(msg)
    assert [s["type"] for s in event["message"]] == ["voice"]
    assert event["message"][0]["data"]["file_id"] == "VOICE1"

    msg2 = _message(
        chat={"id": -10020002, "type": "group", "title": "测试群"},
        text="",
        video={"file_id": "VID1", "mime_type": "video/mp4"},
    )
    ev2 = adapter._normalize_message(msg2)
    assert [s["type"] for s in ev2["message"]] == ["video"]
    assert ev2["message"][0]["data"]["file_id"] == "VID1"


def test_normalize_private_subtype_friend(adapter):
    """私聊 sub_type=friend（OneBot 语义修正）"""
    event = adapter._normalize_message(_message())
    assert event["sub_type"] == "friend"


def test_normalize_group_subtype_normal(adapter):
    """群聊 sub_type=normal"""
    event = adapter._normalize_message(
        _message(chat={"id": -10020002, "type": "group", "title": "测试群"})
    )
    assert event["sub_type"] == "normal"


async def test_send_msg_voice_routes_sendvoice(adapter, monkeypatch):
    """send_msg 含 voice 段 → sendVoice"""
    calls = {}

    async def fake_api(method, **params):
        calls["method"], calls["params"] = method, params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg("private", 10001, [{"type": "voice", "data": {"file_id": "AgVcID"}}])
    assert calls["method"] == "sendVoice"
    assert calls["params"]["voice"] == "AgVcID"


async def test_send_msg_video_routes_sendvideo(adapter, monkeypatch):
    """send_msg 含 video 段 → sendVideo"""
    calls = {}

    async def fake_api(method, **params):
        calls["method"], calls["params"] = method, params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg(
        "private", 10001, [{"type": "video", "data": {"file_id": "http://a/b.mp4"}}]
    )
    assert calls["method"] == "sendVideo"
    assert calls["params"]["video"] == "http://a/b.mp4"


async def test_send_text_with_reply(adapter, monkeypatch):
    """纯文本 + reply 段 → sendMessage 带 reply_to_message_id"""
    calls = {}

    async def fake_api(method, **params):
        calls["params"] = params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg(
        "private",
        10001,
        [
            {"type": "reply", "data": {"message_id": "7"}},
            {"type": "text", "data": {"text": "收到"}},
        ],
    )
    assert calls["params"]["reply_to_message_id"] == 7
    assert calls["params"]["text"] == "收到"


async def test_send_media_with_reply(adapter, monkeypatch):
    """媒体 + reply → 首条媒体携带 reply_to_message_id 与 caption"""
    calls = {}

    async def fake_api(method, **params):
        calls["method"], calls["params"] = method, params
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.send_msg(
        "private",
        10001,
        [
            {"type": "reply", "data": {"message_id": "9"}},
            {"type": "image", "data": {"file_id": "AgABg"}},
            {"type": "text", "data": {"text": "配图"}},
        ],
    )
    assert calls["method"] == "sendPhoto"
    assert calls["params"]["photo"] == "AgABg"
    assert calls["params"]["reply_to_message_id"] == 9
    assert calls["params"]["caption"] == "配图"


def test_resolve_image_refs(adapter):
    """_resolve_image：URL/base64/未知引用分类正确"""
    assert TelegramAdapter._resolve_image("https://a/b.png")[0] == "param"
    assert TelegramAdapter._resolve_image("AgACtgID")[0] == "param"
    kind, _, media = TelegramAdapter._resolve_image("base64://aGVsbG8=")
    assert kind == "upload" and media[1] == b"hello"


# ---------- Telegram 增强：成员变动通知 ----------


def _member_update(
    update_id=10,
    *,
    chat_id=-10020002,
    new_status="member",
    old_status="left",
    user_id=777,
    operator_id=555,
    key="chat_member",
):
    return {
        "update_id": update_id,
        key: {
            "chat": {"id": chat_id, "type": "supergroup", "title": "测试群"},
            "from": {"id": operator_id},
            "new_chat_member": {"status": new_status, "user": {"id": user_id}},
            "old_chat_member": {"status": old_status, "user": {"id": user_id}},
        },
    }


async def test_member_increase_notice(adapter, monkeypatch):
    """成员加入（被邀请）→ group_member_increase，sub_type=invite"""
    adapter.self_id = 999
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)
    await adapter._handle_update(_member_update())
    assert len(seen) == 1
    ev = seen[0]
    assert ev["type"] == "notice"
    assert ev["detail_type"] == "group_member_increase"
    assert ev["sub_type"] == "invite"
    assert ev["group_id"] == "-10020002"
    assert ev["user_id"] == "777"
    assert ev["operator_id"] == "555"
    assert ev["self_id"] == "999"


async def test_member_join_self(adapter, monkeypatch):
    """成员主动加入（无操作者）→ sub_type=join"""
    adapter.self_id = 999
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)
    await adapter._handle_update(_member_update(operator_id=0))
    assert seen and seen[0]["detail_type"] == "group_member_increase"
    assert seen[0]["sub_type"] == "join"


async def test_member_decrease_notice(adapter, monkeypatch):
    """成员离开 → group_member_decrease（leave）"""
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)
    await adapter._handle_update(
        _member_update(new_status="left", old_status="member", operator_id=777)
    )
    assert seen and seen[0]["detail_type"] == "group_member_decrease"
    assert seen[0]["user_id"] == "777"


async def test_member_kick_notice(adapter, monkeypatch):
    """成员被踢（left->new_status=kicked）→ group_member_decrease sub_type=kick"""
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)
    await adapter._handle_update(_member_update(new_status="kicked", old_status="member"))
    assert seen and seen[0]["detail_type"] == "group_member_decrease"
    assert seen[0]["sub_type"] == "kick"


async def test_member_admin_notice(adapter, monkeypatch):
    """成员被设为管理员 → group_admin_set, sub_type=set"""
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)
    await adapter._handle_update(_member_update(new_status="administrator", old_status="member"))
    assert seen and seen[0]["detail_type"] == "group_admin_set"
    assert seen[0]["sub_type"] == "set"


async def test_my_member_notice_uses_self_id(adapter, monkeypatch):
    """my_chat_member（Bot 自身被拉入群）→ user_id = self_id"""
    adapter.self_id = 999
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)
    await adapter._handle_update(_member_update(user_id=999, operator_id=555, key="my_chat_member"))
    assert seen and seen[0]["detail_type"] == "group_member_increase"
    assert seen[0]["user_id"] == "999"


async def test_member_change_non_group_ignored(adapter, monkeypatch):
    """非群聊（如频道）成员变动不产生 notice"""
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)
    upd = _member_update()
    upd["chat_member"]["chat"]["type"] = "channel"
    await adapter._handle_update(upd)
    assert seen == []


# ---------- Telegram 增强：错误分类 ----------


def test_api_error_classification():
    """API 错误按 error_code 归类为对应异常子类"""
    from bot.core.platforms.telegram import (
        TelegramAPIError,
        TelegramForbiddenError,
        TelegramNotFoundError,
        TelegramUnauthorizedError,
        _telegram_error,
    )

    assert isinstance(
        _telegram_error("m", {"error_code": 401, "description": "Unauthorized"}),
        TelegramUnauthorizedError,
    )
    assert isinstance(
        _telegram_error("m", {"error_code": 403, "description": "Forbidden"}),
        TelegramForbiddenError,
    )
    assert isinstance(
        _telegram_error("m", {"error_code": 404, "description": "Not Found"}),
        TelegramNotFoundError,
    )
    # 未知/缺失错误码 → 基类
    assert isinstance(_telegram_error("m", {"error_code": 429}), TelegramAPIError)
    assert isinstance(_telegram_error("m", {}), TelegramAPIError)
    # 子类应继承基类，便于统一捕获
    assert issubclass(TelegramUnauthorizedError, TelegramAPIError)
    assert issubclass(TelegramNotFoundError, TelegramAPIError)


# ---------- Telegram 增强：Token 热更新 ----------


def test_set_token_hot_update(adapter):
    assert adapter.token == "test:token"
    adapter.set_token("new:token")
    assert adapter.token == "new:token"


def test_set_token_same_noop(adapter):
    adapter.set_token("test:token")
    assert adapter.token == "test:token"


def test_set_token_rejects_empty(adapter):
    with pytest.raises(ValueError):
        adapter.set_token("   ")
    with pytest.raises(ValueError):
        adapter.set_token("")


# ---------- Telegram 增强：有限并发与 offset 推进 ----------


async def test_consume_update_swallows_handler_error(adapter, monkeypatch):
    """单条更新处理失败仅记日志，不向调用方抛异常（避免拖垮整批）"""

    async def boom(_update: dict):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(adapter, "_handle_update", boom)
    await adapter._consume_update({"update_id": 1})  # 不应抛出


async def test_poll_loop_advances_offset(adapter, monkeypatch):
    """轮询消费后 offset 推进到 max(update_id)+1，事件全部发射"""
    adapter._running = True
    adapter.poll_interval = 0.01
    calls = {"n": 0}

    async def fake_api(method, **params):
        if method == "getUpdates":
            calls["n"] += 1
            if calls["n"] == 1:
                return [
                    {"update_id": 1, "message": _message(message_id=1, text="a")},
                    {"update_id": 2, "message": _message(message_id=2, text="b")},
                ]
            return []
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    seen = []

    async def rec(event: dict):
        seen.append(event)

    monkeypatch.setattr(adapter, "emit_event", rec)

    task = asyncio.create_task(adapter._poll_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert adapter._offset == 3  # max(1,2)+1
    assert {e["message_id"] for e in seen} == {"1", "2"}
    assert adapter.last_heartbeat > 0


async def test_poll_loop_advances_offset_on_partial_failure(adapter, monkeypatch):
    """某条更新处理失败时 offset 仍推进（避免无限重放）"""
    adapter._running = True
    adapter.poll_interval = 0.01
    calls = {"n": 0}

    async def fake_api(method, **params):
        if method == "getUpdates":
            calls["n"] += 1
            if calls["n"] == 1:
                return [
                    {"update_id": 5, "message": _message(message_id=5, text="ok")},
                    {"update_id": 6, "message": _message(message_id=6, text="boom")},
                ]
            return []
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    seen = []

    async def rec(event: dict):
        seen.append(event)
        if event.get("message_id") == "6":
            raise RuntimeError("handler failure")

    monkeypatch.setattr(adapter, "emit_event", rec)

    task = asyncio.create_task(adapter._poll_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 失败也推进 offset，避免该 update_id 无限重放
    assert adapter._offset == 7
    assert {e["message_id"] for e in seen} == {"5", "6"}


# ---------- Telegram 增强：自适应退避与恢复探测 ----------


def test_backoff_delay_exponential(adapter):
    """指数退避：无失败用轮询间隔，连续失败按 _BACKOFF_MIN^次方，封顶 _BACKOFF_MAX"""
    assert adapter._backoff_delay() == pytest.approx(adapter.poll_interval)
    adapter.consecutive_errors = 1
    assert adapter._backoff_delay() == pytest.approx(_BACKOFF_MIN)
    adapter.consecutive_errors = 3
    assert adapter._backoff_delay() == pytest.approx(_BACKOFF_MIN * 4)
    adapter.consecutive_errors = 99
    assert adapter._backoff_delay() == pytest.approx(_BACKOFF_MAX)


async def test_poll_loop_offline_then_reconnect(adapter, monkeypatch):
    """连续失败触发 on_disconnect，恢复后触发 on_reconnect 并清零计数"""
    adapter._running = True
    adapter.poll_interval = 0.01
    disconnects, reconnects = {"n": 0}, {"n": 0}

    async def on_disconnect():
        disconnects["n"] += 1

    async def on_reconnect():
        reconnects["n"] += 1

    adapter.on_disconnect(on_disconnect)
    adapter.on_reconnect(on_reconnect)
    monkeypatch.setattr(adapter, "_backoff_delay", lambda: 0.01)
    calls = {"n": 0}

    async def fake_api(method, **params):
        if method == "getUpdates":
            calls["n"] += 1
            if calls["n"] <= 6:
                raise RuntimeError("network down")
            return []
        if method == "getMe":
            return {"id": 1, "username": "b1"}
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    task = asyncio.create_task(adapter._poll_loop())
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert disconnects["n"] >= 1  # 连续 >=5 失败广播断连
    assert reconnects["n"] >= 1  # 恢复后广播重连
    assert adapter._disconnected_notified is False
    assert adapter.consecutive_errors == 0
    assert adapter.error_count >= 6
    assert adapter.last_disconnect_time > 0


# ---------- Telegram 增强：Token 热更新自动重验 ----------


def test_set_token_marks_identity_dirty(adapter):
    adapter.set_token("new:tok")
    assert adapter.token == "new:tok"
    assert adapter._identity_dirty is True


async def test_refresh_identity_updates_self_id(adapter, monkeypatch):
    async def fake_api(method, **params):
        assert method == "getMe"
        return {"id": 555, "username": "botname"}

    monkeypatch.setattr(adapter, "_api", fake_api)
    await adapter.refresh_identity()
    assert adapter.self_id == 555
    assert adapter.username == "botname"
    assert adapter._identity_dirty is False


async def test_poll_loop_auto_revalidates_after_token_hotswap(adapter, monkeypatch):
    """set_token 后轮询下一轮自动重验身份并刷新 self_id/username"""
    adapter._running = True
    adapter.poll_interval = 0.01
    adapter.set_token("new:tok")  # 置 _identity_dirty

    async def fake_api(method, **params):
        if method == "getUpdates":
            return []
        if method == "getMe":
            return {"id": 555, "username": "botname"}
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    task = asyncio.create_task(adapter._poll_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert adapter._identity_dirty is False
    assert adapter.self_id == 555
    assert adapter.username == "botname"


# ---------- Telegram 增强：HTTP 超时/重试可配置化 ----------


def test_request_timeout_used_by_client(adapter):
    assert adapter._client().timeout.connect == pytest.approx(adapter.request_timeout)
    assert adapter._client().timeout.read == pytest.approx(adapter.request_timeout)
    # 未显式传参时默认 > 长轮询 timeout，避免提前断连
    assert adapter.request_timeout == pytest.approx(40.0)


def test_make_platform_passes_new_options():
    inst = make_platform(
        SimpleNamespace(
            name="telegram",
            enabled=True,
            token="t",
            poll_interval=2.0,
            request_timeout=55.0,
            max_retries=3,
        )
    )
    assert inst.request_timeout == 55.0
    assert inst.max_retries == 3


async def test_api_retries_transport_error(adapter, monkeypatch):
    adapter.max_retries = 2
    attempt = {"n": 0}

    async def no_sleep(_delay):
        return None

    async def flaky(url, **payload):
        attempt["n"] += 1
        if attempt["n"] < 3:
            raise httpx.TransportError("boom")
        return {"chat_id": 1}

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_api_once", flaky)
    result = await adapter._api("getChatMemberCount", chat_id=1)
    assert result == {"chat_id": 1}
    assert attempt["n"] == 3  # 首次失败 + 2 次重试后成功


async def test_api_retries_exhausted_raises(adapter, monkeypatch):
    adapter.max_retries = 1

    async def no_sleep(_delay):
        return None

    async def always_fail(url, **payload):
        raise httpx.TransportError("boom")

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_api_once", always_fail)
    with pytest.raises(TelegramAPIError):
        await adapter._api("getChatMemberCount", chat_id=1)


async def test_api_no_retry_on_business_error(adapter, monkeypatch):
    """业务错误（ok=false）不重试，直接抛分类异常"""
    adapter.max_retries = 3
    attempt = {"n": 0}

    async def fake_once(url, **payload):
        attempt["n"] += 1
        raise _telegram_404()

    monkeypatch.setattr(adapter, "_api_once", fake_once)
    with pytest.raises(TelegramNotFoundError):
        await adapter._api("getChatMemberCount", chat_id=1)
    assert attempt["n"] == 1  # 只调用一次，不因业务错误重试


# ---------- Telegram 增强：可观测性 ----------


def test_status_info_fields():
    a = TelegramAdapter(token="t")
    assert a.status_info()["connection_state"] == "stopped"
    a._running = True
    assert a.status_info()["connection_state"] == "connected"
    a.consecutive_errors = 2
    info = a.status_info()
    assert info["connection_state"] == "connecting"
    assert info["consecutive_errors"] == 2
    assert info["backoff"] > 0
    assert info["error_count"] == 0


def _telegram_404() -> TelegramAPIError:
    return TelegramNotFoundError("Telegram API 错误 (m): 404", error_code=404, description="404")
