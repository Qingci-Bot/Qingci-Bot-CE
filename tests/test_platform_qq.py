# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""QQ 官方机器人适配器测试

覆盖：事件归一化、openid 稳定映射、去重、被动回复（msg_id / msg_seq）、
富媒体上传与发送、token 缓存与刷新、网关鉴权、错误分类、能力透传，
以及「QQ 事件 → 插件回复 → QQ 适配器」的端到端路由。
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import httpx
import pytest

from bot.core.platforms.base import make_platform
from bot.core.platforms.qq import (
    OP_IDENTIFY,
    OP_RESUME,
    QQAdapter,
    QQAPIError,
    QQAuthError,
    QQDuplicateSeqError,
    QQPassiveWindowError,
    QQPermissionError,
    _qq_error,
)

GROUP_OPENID = "B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5"
USER_OPENID = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"


@pytest.fixture
def adapter() -> QQAdapter:
    qq = QQAdapter(app_id="1000001", app_secret="s3cret")
    qq.self_id = "9000001"
    return qq


def _group_event(**kw) -> dict:
    data = {
        "id": "ROBOT1.0_group_msg",
        "content": " /ping",
        "group_openid": GROUP_OPENID,
        "author": {"id": "u-1", "member_openid": USER_OPENID, "username": "张三"},
        "timestamp": "2026-11-17T12:00:00+08:00",
    }
    data.update(kw)
    return data


def _c2c_event(**kw) -> dict:
    data = {
        "id": "ROBOT1.0_c2c_msg",
        "content": "hello",
        "author": {"id": "u-2", "user_openid": USER_OPENID, "username": "李四"},
        "timestamp": "2026-11-17T12:00:00+08:00",
    }
    data.update(kw)
    return data


# ---------- 构造与配置 ----------


def test_make_platform_creates_qq():
    cfg = SimpleNamespace(
        name="qq",
        enabled=True,
        app_id="1000001",
        app_secret="s3cret",
        sandbox=True,
        intents=1 << 25,
        api_base="",
        request_timeout=15.0,
        max_retries=3,
    )
    inst = make_platform(cfg)
    assert isinstance(inst, QQAdapter)
    assert inst.app_id == "1000001"
    assert inst.sandbox is True
    # 沙箱环境自动切到沙箱 OpenAPI 地址
    assert inst.api_base == "https://sandbox.api.sgroup.qq.com"
    assert inst.max_retries == 3


def test_make_platform_disabled_returns_none():
    assert make_platform(SimpleNamespace(name="qq", enabled=False)) is None


def test_default_api_base_is_production(adapter):
    assert adapter.api_base == "https://api.sgroup.qq.com"


# ---------- 事件归一化 ----------


def test_normalize_group_message(adapter):
    event = adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", _group_event())
    assert event is not None
    assert event["type"] == "message"
    assert event["detail_type"] == "group"
    assert event["platform"] == "qq"
    assert event["self_id"] == "9000001"
    # 平台附加的前导空格被去掉，但正文其它部分保持原样
    assert event["alt_message"] == "/ping"
    assert event["plain_text"] == "/ping"
    assert event["group_id"] and event["group_id"].isdigit()
    assert event["user_id"].isdigit()
    assert event["sender"]["nickname"] == "张三"
    # 群消息只在 @机器人 时推送；正文已去掉 @ 前缀，故显式声明触发
    assert event["is_at_bot"] is True
    assert event["message"][0] == {"type": "text", "data": {"text": "/ping"}}


def test_normalize_private_message(adapter):
    event = adapter._normalize_message("C2C_MESSAGE_CREATE", _c2c_event())
    assert event is not None
    assert event["detail_type"] == "private"
    assert event["group_id"] == ""
    assert event["sub_type"] == "friend"
    assert event["is_at_bot"] is False
    assert event["user_id"].isdigit()


def test_normalize_requires_identity(adapter):
    """既无 group_openid 也无 user_openid 的事件不可归一化"""
    assert adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", {"id": "x"}) is None
    assert adapter._normalize_message("C2C_MESSAGE_CREATE", {"id": "x"}) is None


def test_normalize_attachments_mapped_by_content_type(adapter):
    data = _group_event(
        attachments=[
            {
                "url": "https://gchat.qpic.cn/a.jpg",
                "content_type": "image/jpeg",
                "filename": "a.jpg",
            },
            {"url": "https://gchat.qpic.cn/v.mp4", "content_type": "video/mp4"},
            {
                "url": "https://gchat.qpic.cn/v.silk",
                "content_type": "voice",
                "voice_wav_url": "https://gchat.qpic.cn/v.wav",
            },
            {
                "url": "https://gchat.qpic.cn/f.pdf",
                "content_type": "file",
                "filename": "f.pdf",
            },
            {"content_type": "image/png"},  # 缺 url，忽略
        ]
    )
    event = adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", data)
    assert event is not None
    types = [seg["type"] for seg in event["message"] if seg["type"] != "text"]
    assert types == ["image", "video", "voice", "file"]
    assert event["images"] == ["https://gchat.qpic.cn/a.jpg"]
    voice = next(seg for seg in event["message"] if seg["type"] == "voice")
    assert voice["data"]["wav_url"] == "https://gchat.qpic.cn/v.wav"
    file_seg = next(seg for seg in event["message"] if seg["type"] == "file")
    assert file_seg["data"]["name"] == "f.pdf"


def test_normalize_timestamp_falls_back(adapter):
    event = adapter._normalize_message("C2C_MESSAGE_CREATE", _c2c_event(timestamp="not-a-time"))
    assert event is not None
    assert event["time"] <= time.time() + 1


# ---------- openid ↔ 整数 ID ----------


def test_stable_id_is_deterministic_and_type_scoped(adapter):
    a = adapter._stable_id("u", USER_OPENID)
    b = QQAdapter(app_id="x", app_secret="y")._stable_id("u", USER_OPENID)
    assert a == b  # 重启后仍一致，会话与统计不漂移
    assert a > 0
    assert adapter._stable_id("g", USER_OPENID) != a  # 类型前缀隔离
    assert adapter._stable_id("u", "") == 0


def test_normalize_registers_reverse_mapping(adapter):
    event = adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", _group_event())
    assert event is not None
    # 回发消息依赖反向表还原因果 openid
    assert adapter._openid_of(int(event["group_id"])) == GROUP_OPENID
    assert adapter._openid_of(int(event["user_id"])) == USER_OPENID
    assert adapter._nickname_by_id[int(event["user_id"])] == "张三"


# ---------- 入站上报与去重 ----------


async def test_handle_message_event_emits_once(adapter):
    seen: list[dict] = []
    adapter.on_event(seen.append)
    data = _group_event()
    await adapter._handle_message_event("GROUP_AT_MESSAGE_CREATE", data)
    # 平台可能重复推送同一条消息
    await adapter._handle_message_event("GROUP_AT_MESSAGE_CREATE", data)
    assert len(seen) == 1
    assert seen[0]["platform"] == "qq"


async def test_handle_message_event_skips_without_identity(adapter):
    seen: list[dict] = []
    adapter.on_event(seen.append)
    await adapter._handle_message_event("GROUP_AT_MESSAGE_CREATE", {"id": "no-id"})
    assert seen == []


# ---------- 发送：被动回复 ----------


class _Recorder:
    """替换 _api：记录调用并按需返回"""

    def __init__(self, replies: list[dict] | None = None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self._replies = list(replies or [])

    async def __call__(self, method: str, path: str, *, json_body: dict | None = None) -> dict:
        self.calls.append((method, path, json_body))
        if self._replies:
            return self._replies.pop(0)
        return {"id": "sent"}


async def test_send_text_uses_passive_msg_id_and_increments_seq(adapter, monkeypatch):
    event = adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", _group_event())
    assert event is not None
    target = int(event["group_id"])
    rec = _Recorder()
    monkeypatch.setattr(adapter, "_api", rec)

    await adapter.send_msg("group", target, "pong")
    await adapter.send_msg("group", target, "again")

    first, second = rec.calls
    assert first[1] == f"/v2/groups/{GROUP_OPENID}/messages"
    assert first[2] == {
        "msg_type": 0,
        "content": "pong",
        "msg_id": "ROBOT1.0_group_msg",
        "msg_seq": 1,
    }
    # 同一 msg_id 的 msg_seq 必须递增，否则平台判重（40054005）
    assert second[2]["msg_seq"] == 2


async def test_send_text_without_context_sends_active(adapter, monkeypatch):
    """没有入站上下文（窗口过期/主动发送）时不带 msg_id，由平台决定是否允许"""
    target = adapter._remember("g", GROUP_OPENID)
    rec = _Recorder()
    monkeypatch.setattr(adapter, "_api", rec)
    await adapter.send_msg("group", target, "hi")
    assert rec.calls[0][2] == {"msg_type": 0, "content": "hi"}


async def test_send_rejects_unknown_target(adapter):
    with pytest.raises(ValueError, match="目标会话不在已知列表内"):
        await adapter.send_msg("group", 12345, "hi")


async def test_send_empty_message_rejected(adapter):
    target = adapter._remember("g", GROUP_OPENID)
    with pytest.raises(ValueError, match="消息内容为空"):
        await adapter.send_msg("group", target, "")


async def test_send_private_uses_user_path(adapter, monkeypatch):
    event = adapter._normalize_message("C2C_MESSAGE_CREATE", _c2c_event())
    assert event is not None
    rec = _Recorder()
    monkeypatch.setattr(adapter, "_api", rec)
    await adapter.send_msg("private", int(event["user_id"]), "pong")
    assert rec.calls[0][1] == f"/v2/users/{USER_OPENID}/messages"


async def test_send_respects_passive_quota(adapter, monkeypatch):
    """群聊同一 msg_id 最多回复 5 条，超出应给出明确错误而非硬碰平台"""
    event = adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", _group_event())
    assert event is not None
    target = int(event["group_id"])
    monkeypatch.setattr(adapter, "_api", _Recorder())
    for _ in range(5):
        await adapter.send_msg("group", target, "ok")
    with pytest.raises(QQPassiveWindowError, match="被动回复次数不足"):
        await adapter.send_msg("group", target, "overflow")


async def test_send_uses_reply_segment_message_id(adapter, monkeypatch):
    """显式 reply 段优先于「该会话最近一条消息」"""
    target = adapter._remember("g", GROUP_OPENID)
    adapter._reply_ctx[target] = ("ROBOT1.0_recent", time.time() + 300)
    rec = _Recorder()
    monkeypatch.setattr(adapter, "_api", rec)
    message = [
        {"type": "reply", "data": {"message_id": "ROBOT1.0_quoted"}},
        {"type": "text", "data": {"text": "收到"}},
    ]
    await adapter.send_msg("group", target, message)
    assert rec.calls[0][2]["msg_id"] == "ROBOT1.0_quoted"


# ---------- 发送：富媒体 ----------


async def test_send_media_uploads_then_sends(adapter, monkeypatch):
    event = adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", _group_event())
    assert event is not None
    target = int(event["group_id"])
    rec = _Recorder([{"file_info": "FILEINFO-1"}, {"id": "msg-1"}])
    monkeypatch.setattr(adapter, "_api", rec)

    message = [
        {"type": "text", "data": {"text": "看图"}},
        {"type": "image", "data": {"file_id": "https://cdn.example.com/a.png"}},
    ]
    await adapter.send_msg("group", target, message)

    upload, send = rec.calls
    assert upload[1] == f"/v2/groups/{GROUP_OPENID}/files"
    assert upload[2] == {
        "file_type": 1,
        "srv_send_msg": False,
        "url": "https://cdn.example.com/a.png",
    }
    assert send[1] == f"/v2/groups/{GROUP_OPENID}/messages"
    assert send[2] == {
        "msg_type": 7,
        "media": {"file_info": "FILEINFO-1"},
        "content": "看图",
        "msg_id": "ROBOT1.0_group_msg",
        "msg_seq": 1,
    }


async def test_send_multiple_images_split_and_advance_seq(adapter, monkeypatch):
    """单条富媒体消息只能承载一个媒体，多图必须拆条且序号连续"""
    event = adapter._normalize_message("GROUP_AT_MESSAGE_CREATE", _group_event())
    assert event is not None
    target = int(event["group_id"])
    rec = _Recorder(
        [
            {"file_info": "F1"},
            {"id": "m1"},
            {"file_info": "F2"},
            {"id": "m2"},
        ]
    )
    monkeypatch.setattr(adapter, "_api", rec)
    message = [
        {"type": "image", "data": {"file_id": "https://cdn.example.com/1.png"}},
        {"type": "image", "data": {"file_id": "https://cdn.example.com/2.png"}},
    ]
    await adapter.send_msg("group", target, message)
    sends = [call for call in rec.calls if call[1].endswith("/messages")]
    assert [s[2]["msg_seq"] for s in sends] == [1, 2]
    # 计数器已推进到 2，下一条回复从 3 开始
    assert adapter._msg_seq["ROBOT1.0_group_msg"] == 2


async def test_send_local_file_uses_base64_payload(adapter, monkeypatch, tmp_path):
    target = adapter._remember("g", GROUP_OPENID)
    path = tmp_path / "report.txt"
    path.write_bytes(b"hello")
    rec = _Recorder([{"file_info": "F"}, {"id": "m"}])
    monkeypatch.setattr(adapter, "_api", rec)
    await adapter.send_msg("group", target, [{"type": "file", "data": {"file": str(path)}}])
    upload = rec.calls[0]
    assert upload[2]["file_type"] == 4
    assert upload[2]["file_name"] == "report.txt"
    assert upload[2]["file_data"] == "aGVsbG8="


async def test_upload_requires_file_info(adapter, monkeypatch):
    target = adapter._remember("g", GROUP_OPENID)
    monkeypatch.setattr(adapter, "_api", _Recorder([{}]))
    with pytest.raises(QQAPIError, match="file_info"):
        await adapter.send_msg(
            "group", target, [{"type": "image", "data": {"file_id": "https://x/y.png"}}]
        )


# ---------- 出站段解析 ----------


def test_parse_outgoing_renders_mention_as_nickname(adapter):
    uid = adapter._remember("u", USER_OPENID, "张三")
    media, text, reply_id = adapter._parse_outgoing(
        [
            {"type": "reply", "data": {"message_id": "M1"}},
            {"type": "mention", "data": {"user_id": str(uid)}},
            {"type": "text", "data": {"text": "你好"}},
        ]
    )
    assert media == []
    assert text == "@张三 你好"
    assert reply_id == "M1"


def test_parse_outgoing_drops_unknown_mention(adapter):
    """未知 openid 不渲染成内部整数 ID，避免把哈希值发给用户"""
    _, text, _ = adapter._parse_outgoing(
        [{"type": "mention", "data": {"user_id": "123"}}, {"type": "text", "data": {"text": "hi"}}]
    )
    assert text == "hi"


# ---------- 鉴权 ----------


def _mock_http(adapter, handler) -> None:
    adapter._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_access_token_cached(adapter):
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        assert json.loads(request.content) == {"appId": "1000001", "clientSecret": "s3cret"}
        return httpx.Response(200, json={"access_token": "TOKEN-1", "expires_in": "7200"})

    _mock_http(adapter, handler)
    try:
        assert await adapter._access_token() == "TOKEN-1"
        assert await adapter._access_token() == "TOKEN-1"
    finally:
        await adapter._http.aclose()
    assert len(hits) == 1  # 有效期内复用缓存

    # 临近过期（60 秒内）应换发新 token
    adapter._token_expire_at = time.time() + 30
    _mock_http(adapter, lambda req: httpx.Response(200, json={"access_token": "TOKEN-2"}))
    try:
        assert await adapter._access_token() == "TOKEN-2"
    finally:
        await adapter._http.aclose()


async def test_access_token_failure_classified_as_auth(adapter):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 100016, "message": "invalid appid or secret"})

    _mock_http(adapter, handler)
    try:
        with pytest.raises(QQAuthError, match="AppID / AppSecret"):
            await adapter._access_token()
    finally:
        await adapter._http.aclose()


async def test_api_sends_bearer_header(adapter):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app/getAppAccessToken":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "ok"})

    _mock_http(adapter, handler)
    try:
        out = await adapter._api("POST", "/v2/users/u/messages", json_body={"content": "hi"})
    finally:
        await adapter._http.aclose()
    assert out == {"id": "ok"}
    assert seen["auth"] == "QQBot T"
    assert seen["path"] == "/v2/users/u/messages"
    assert seen["body"] == {"content": "hi"}


async def test_api_raises_on_business_error(adapter):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app/getAppAccessToken":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        return httpx.Response(200, json={"err_code": 40034128, "message": "被动回复超限"})

    _mock_http(adapter, handler)
    try:
        with pytest.raises(QQPassiveWindowError):
            await adapter._api("POST", "/v2/groups/g/messages", json_body={})
    finally:
        await adapter._http.aclose()


# ---------- 错误分类 ----------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"code": 11241}, QQAuthError),
        ({"code": 11253}, QQPermissionError),
        ({"code": 40034005}, QQPassiveWindowError),
        ({"err_code": 40034128}, QQPassiveWindowError),
        ({"code": 40054005}, QQDuplicateSeqError),
        ({"code": 40054007}, QQAPIError),
    ],
)
def test_error_classification(payload, expected):
    err = _qq_error("POST /x", {**payload, "message": "boom", "trace_id": "t1"})
    assert isinstance(err, expected)
    assert err.trace_id == "t1"


# ---------- 网关 ----------


async def test_send_identity_without_session_identifies(adapter):
    sent: list[dict] = []

    class FakeWS:
        async def send(self, raw):
            sent.append(json.loads(raw))

    adapter._token = "T"
    adapter._token_expire_at = time.time() + 3600
    await adapter._send_identity(FakeWS())
    assert sent[0]["op"] == OP_IDENTIFY
    assert sent[0]["d"]["token"] == "QQBot T"
    assert sent[0]["d"]["intents"] == 1 << 25
    assert sent[0]["d"]["shard"] == [0, 1]


async def test_send_identity_with_session_resumes(adapter):
    sent: list[dict] = []

    class FakeWS:
        async def send(self, raw):
            sent.append(json.loads(raw))

    adapter._token = "T"
    adapter._token_expire_at = time.time() + 3600
    adapter._session_id = "sess-1"
    adapter._seq = 42
    await adapter._send_identity(FakeWS())
    assert sent[0] == {
        "op": OP_RESUME,
        "d": {"token": "QQBot T", "session_id": "sess-1", "seq": 42},
    }


def test_hello_interval_parsed(adapter):
    assert adapter._hello_interval({"d": {"heartbeat_interval": 41250}}) == 41.25
    assert adapter._hello_interval({"d": {}}) == 30.0
    # 异常值不得导致心跳风暴
    assert adapter._hello_interval({"d": {"heartbeat_interval": 10}}) == 5.0


async def test_on_dispatch_ready_sets_state_and_notifies(adapter):
    connected: list[bool] = []
    adapter.on_connect(lambda: connected.append(True))
    adapter._running = True  # 网关运行中才会收到 READY
    await adapter._on_dispatch(
        {
            "op": 0,
            "s": 1,
            "t": "READY",
            "d": {
                "session_id": "sess-9",
                "user": {"id": "9000001", "username": "测试机器人"},
            },
        }
    )
    assert adapter._ready is True
    assert adapter._session_id == "sess-9"
    assert adapter.self_id == "9000001"
    assert adapter.username == "测试机器人"
    assert adapter.is_connected is True
    assert connected == [True]


async def test_on_dispatch_message_emits_normalized_event(adapter):
    seen: list[dict] = []
    adapter.on_event(seen.append)
    await adapter._on_dispatch(
        {"op": 0, "s": 3, "t": "GROUP_AT_MESSAGE_CREATE", "d": _group_event()}
    )
    assert len(seen) == 1
    assert seen[0]["detail_type"] == "group"
    assert adapter._seq == 3


async def test_on_dispatch_unknown_event_goes_to_metaevent(adapter):
    metas: list[dict] = []
    adapter.on_metaevent(metas.append)
    await adapter._on_dispatch(
        {"op": 0, "s": 5, "t": "GROUP_ADD_ROBOT", "d": {"group_openid": GROUP_OPENID}}
    )
    assert metas and metas[0]["detail_type"] == "group_add_robot"


def test_status_info_shape(adapter):
    info = adapter.status_info()
    assert info["connection_state"] == "stopped"
    assert info["sandbox"] is False
    assert info["app_id"] == "1000001"
    assert info["known_sessions"] == 0


# ---------- 能力透传 ----------


async def test_call_api_maps_send_actions(adapter, monkeypatch):
    calls: list[tuple] = []

    async def fake_send(message_type, target_id, message):
        calls.append((message_type, target_id, message))
        return {"id": "x"}

    monkeypatch.setattr(adapter, "send_msg", fake_send)
    await adapter.call_api("send_group_msg", {"group_id": 7, "message": "hi"})
    assert calls == [("group", 7, "hi")]


async def test_call_api_rejects_group_admin_actions(adapter):
    with pytest.raises(NotImplementedError, match="不支持 OneBot 动作"):
        await adapter.call_api("set_group_kick", {"group_id": 1, "user_id": 2})


async def test_call_api_file_ref_passthrough(adapter):
    out = await adapter.call_api("get_image", {"file_id": "https://cdn/x.png"})
    assert out == {"file_id": "https://cdn/x.png", "url": "https://cdn/x.png"}


async def test_call_api_raw_path_escape_hatch(adapter, monkeypatch):
    seen: list[tuple] = []

    async def fake_api(method, path, *, json_body=None):
        seen.append((method, path, json_body))
        return {"ok": True}

    monkeypatch.setattr(adapter, "_api", fake_api)
    out = await adapter.call_api("/v2/menu", {"_method": "GET"})
    assert out == {"ok": True}
    assert seen == [("GET", "/v2/menu", None)]


# ---------- 端到端：事件 → 插件 → 适配器 ----------


class RecordingQQ(QQAdapter):
    """记录发送调用，避免真实网络"""

    def __init__(self):
        super().__init__(app_id="app", app_secret="sec")
        self.self_id = "9000001"
        self.sent: list[tuple] = []

    async def start(self) -> None:
        self._running = True
        self._ready = True

    async def stop(self) -> None:
        self._running = False
        self._ready = False

    async def send_msg(self, message_type, target_id, message):
        self.sent.append((message_type, target_id, message))
        return {"id": "sent"}


def _register(bot, plugin):
    from bot.plugin import PluginStatus

    plugin._status = PluginStatus.LOADED
    for m in plugin.matchers or []:
        if not m.owner:
            m.owner = plugin.name
    bot.plugin_manager._plugins[plugin.name] = plugin
    bot.plugin_manager._invalidate_matchers_cache()
    return plugin


async def test_qq_group_event_routes_reply_back_to_qq(bot):
    """QQ 群消息 /ping → 插件回复 → 经 QQ 适配器发出（含 @ 与回复段）"""
    from qingci_plugin_sdk import PluginBase, on_command

    qq = RecordingQQ()
    bot.platforms["qq"] = qq

    class EchoPlugin(PluginBase):
        name = "qqecho"
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

    event = qq._normalize_message("GROUP_AT_MESSAGE_CREATE", _group_event())
    assert event is not None
    reply = await bot.send(event)
    assert reply == "pong"

    ctx = bot.dispatcher.dispatch(event)
    assert ctx.platform == "qq"
    assert ctx.message_type == "group"
    assert ctx.is_at_bot is True  # 显式声明生效（无 mention 段）

    await bot._send_reply(ctx, reply)
    assert len(qq.sent) == 1
    message_type, target_id, message = qq.sent[0]
    assert message_type == "group"
    # target 为归一化后的整数群 ID，适配器经反向表还原 openid
    assert str(target_id) == event["group_id"]
    assert message == "pong"


async def test_qq_private_event_routes_reply(bot):
    from qingci_plugin_sdk import PluginBase, on_command

    qq = RecordingQQ()
    bot.platforms["qq"] = qq

    class EchoPlugin(PluginBase):
        name = "qqecho2"
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

    event = qq._normalize_message("C2C_MESSAGE_CREATE", _c2c_event(content="/ping"))
    assert event is not None
    reply = await bot.send(event)
    assert reply == "pong"
    ctx = bot.dispatcher.dispatch(event)
    await bot._send_reply(ctx, reply)
    assert qq.sent[0][0] == "private"
    assert str(qq.sent[0][1]) == event["user_id"]


async def test_dispatcher_honours_explicit_is_at_bot(bot):
    """v12 事件显式 is_at_bot 生效，无需伪 mention 段污染正文"""
    event = {
        "type": "message",
        "detail_type": "group",
        "id": "m1",
        "platform": "qq",
        "self_id": "9000001",
        "message_id": "m1",
        "message": [{"type": "text", "data": {"text": "hi"}}],
        "alt_message": "hi",
        "user_id": "111",
        "group_id": "222",
        "is_at_bot": True,
    }
    ctx = bot.dispatcher.dispatch(event)
    assert ctx.is_at_bot is True
    assert ctx.plain_text == "hi"  # 正文未被注入 @ 前缀


async def test_dispatcher_ignores_is_at_bot_when_mentions_present(bot):
    """有真实 mention 段时以段为准，不被事件字段覆盖"""
    event = {
        "type": "message",
        "detail_type": "group",
        "id": "m2",
        "platform": "onebot12",
        "self_id": "999",
        "message_id": "m2",
        "message": [
            {"type": "mention", "data": {"user_id": "123"}},
            {"type": "text", "data": {"text": "hi"}},
        ],
        "alt_message": "hi",
        "user_id": "111",
        "group_id": "222",
        "is_at_bot": True,  # 与真实 @ 列表冲突，应以段为准
    }
    ctx = bot.dispatcher.dispatch(event)
    assert ctx.is_at_bot is False
