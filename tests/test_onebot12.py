"""OneBot 12 原生反向 WebSocket 适配器测试

验证：
- make_platform 配置解析（禁用返回 None / 启用创建适配器）
- 反向 WS 事件直通：v12 事件无需翻译直达事件处理器（注入 platform=onebot12）
- 元事件走 emit_metaevent 通道
- 动作 JSON-RPC：send_msg / call_api 发送 {action, params, echo} 并匹配响应
- access_token 校验（Authorization: Bearer / query 参数）
- 连接状态与 self_id / impl 记录
"""

import asyncio

import aiohttp
import pytest

from bot.config import OneBot12Config
from bot.core.platforms.base import make_platform
from bot.core.platforms.onebot12 import OneBot12Adapter

TEST_PORT = 3021
WS_URL = f"ws://127.0.0.1:{TEST_PORT}/"


def _v12_message_event(**kw) -> dict:
    event = {
        "id": "evt-1",
        "impl": "napcat",
        "platform": "qq",
        "self_id": "30001",
        "time": 1787000000.0,
        "type": "message",
        "detail_type": "group",
        "sub_type": "",
        "message_id": "6283",
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "alt_message": "你好",
        "user_id": "10001",
        "group_id": "20001",
    }
    event.update(kw)
    return event


@pytest.fixture
async def adapter() -> None:
    a = OneBot12Adapter(host="127.0.0.1", port=TEST_PORT, enabled=True)
    await a.start()
    yield a
    await a.stop()


# ---------- make_platform 配置解析 ----------


def test_make_platform_disabled_returns_none():
    cfg = OneBot12Config(enabled=False)
    assert make_platform(cfg) is None


def test_make_platform_enabled():
    cfg = OneBot12Config(enabled=True, host="127.0.0.1", port=3022)
    a = make_platform(cfg)
    assert isinstance(a, OneBot12Adapter)
    assert a.name == "onebot12"
    assert a.port == 3022


# ---------- 事件直通 ----------


@pytest.mark.asyncio
async def test_ws_event_dispatch(adapter):
    """v12 事件直通事件处理器（platform 覆盖为 onebot12，self_id/impl 记录）"""
    received = asyncio.Queue()
    adapter.on_event(lambda evt: received.put_nowait(evt))

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            await ws.send_json(_v12_message_event())

            evt = await asyncio.wait_for(received.get(), timeout=5)
            assert evt["type"] == "message"
            assert evt["detail_type"] == "group"
            assert evt["platform"] == "onebot12"  # 内部路由语义
            assert evt["user_id"] == "10001"
            assert evt["message"][0]["type"] == "text"

    assert adapter.self_id == "30001"
    assert adapter.impl == "napcat"
    assert adapter.is_connected is False  # 连接已关闭


@pytest.mark.asyncio
async def test_ws_metaevent(adapter):
    """meta 元事件走 emit_metaevent 通道"""
    meta = asyncio.Queue()
    adapter.on_metaevent(lambda evt: meta.put_nowait(evt))

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            await ws.send_json(
                {
                    "id": "evt-meta",
                    "impl": "napcat",
                    "platform": "qq",
                    "self_id": "30001",
                    "time": 1787000000.0,
                    "type": "meta",
                    "detail_type": "heartbeat",
                    "sub_type": "",
                    "interval": 3000,
                }
            )
            evt = await asyncio.wait_for(meta.get(), timeout=5)
            assert evt["type"] == "meta"
            assert evt["detail_type"] == "heartbeat"
            assert evt["platform"] == "onebot12"


# ---------- 动作 JSON-RPC ----------


@pytest.mark.asyncio
async def test_send_msg_jsonrpc(adapter):
    """send_msg 发送 send_message 动作并匹配 echo 响应"""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            # 先让适配器记录 self_id（实际场景下协议端会先推事件）
            await ws.send_json(_v12_message_event())

            fut = asyncio.ensure_future(
                adapter.send_msg(
                    "group",
                    20001,
                    [{"type": "text", "data": {"text": "回复"}}],
                )
            )
            req = await asyncio.wait_for(ws.receive_json(), timeout=5)
            assert req["action"] == "send_message"
            assert req["params"]["detail_type"] == "group"
            assert req["params"]["group_id"] == "20001"
            assert req["params"]["message"] == [{"type": "text", "data": {"text": "回复"}}]
            echo = req["echo"]
            assert echo

            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": "abc123", "time": 1787000000.0},
                    "message": "",
                    "echo": echo,
                }
            )
            data = await asyncio.wait_for(fut, timeout=5)
            assert data["message_id"] == "abc123"


@pytest.mark.asyncio
async def test_call_api_private_text(adapter):
    """call_api 透传；纯文本 message 原样传递"""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            fut = asyncio.ensure_future(
                adapter.call_api(
                    "send_message", {"detail_type": "private", "user_id": "10001", "message": "hi"}
                )
            )
            req = await asyncio.wait_for(ws.receive_json(), timeout=5)
            assert req["action"] == "send_message"
            assert req["params"]["message"] == "hi"
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": "m1"},
                    "message": "",
                    "echo": req["echo"],
                }
            )
            assert (await asyncio.wait_for(fut, timeout=5))["message_id"] == "m1"


@pytest.mark.asyncio
async def test_call_api_error_response(adapter):
    """动作失败：status != ok 抛出 RuntimeError 并带实现端 message"""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            with pytest.raises(RuntimeError, match="send_message"):
                fut = asyncio.ensure_future(adapter.call_api("send_message", {}))
                req = await asyncio.wait_for(ws.receive_json(), timeout=5)
                await ws.send_json(
                    {
                        "status": "failed",
                        "retcode": 34001,
                        "data": None,
                        "message": "network down",
                        "echo": req["echo"],
                    }
                )
                await asyncio.wait_for(fut, timeout=5)


@pytest.mark.asyncio
async def test_call_api_timeout(adapter):
    """动作响应超时抛出 TimeoutError"""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL):
            with pytest.raises(TimeoutError):
                await adapter.call_api("get_status", {}, timeout=0.3)


@pytest.mark.asyncio
async def test_call_api_no_client():
    """无协议端连接时抛错（不等待）"""
    a = OneBot12Adapter(host="127.0.0.1", port=TEST_PORT + 1, enabled=False)
    with pytest.raises(RuntimeError, match="无可用连接"):
        await a.call_api("send_message", {})
    await a.stop()


# ---------- access_token 校验 ----------


@pytest.mark.asyncio
async def test_access_token_required():
    """配置 access_token 后，未携带 token 的连接被拒绝"""
    a = OneBot12Adapter(host="127.0.0.1", port=TEST_PORT + 2, enabled=True, access_token="secret")
    await a.start()
    try:
        with pytest.raises(aiohttp.WSServerHandshakeError):
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"ws://127.0.0.1:{TEST_PORT + 2}/"):
                    pass

        # 携带 Authorization: Bearer 可以连接
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"ws://127.0.0.1:{TEST_PORT + 2}/", headers={"Authorization": "Bearer secret"}
            ) as ws:
                await ws.send_json(_v12_message_event())
                await asyncio.sleep(0.2)
        assert a.is_connected is False  # 连接关闭后无客户端
    finally:
        await a.stop()


@pytest.mark.asyncio
async def test_heartbeat_and_state(adapter):
    """收到事件后 last_heartbeat 更新、连接状态翻转"""
    assert adapter.last_heartbeat == 0.0
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            assert adapter.is_connected is True
            await ws.send_json(_v12_message_event())
            for _ in range(50):
                if adapter.last_heartbeat > 0:
                    break
                await asyncio.sleep(0.05)
            assert adapter.last_heartbeat > 0
    assert adapter.is_connected is False
