"""MCP 桥接器测试

不依赖真实 mcp 包：向 sys.modules 注入假 mcp 模块，验证：
- connect_servers：空配置、未命名跳过、连接失败容错
- _connect_one：stdio 路径（initialize + list_tools）
- register_tools：注册、去重、schema 归一化
- _make_handler：文本/结构化/失败/空结果
- close：幂等清理
"""

import sys
import types
from types import SimpleNamespace

import pytest

from bot.llm.mcp import MCPBridge
from bot.llm.tools import ToolRegistry


class FakeTool:
    def __init__(self, name="t1", description="desc", input_schema=None, dump_schema=False):
        self.name = name
        self.description = description
        if dump_schema:
            self.inputSchema = SimpleNamespace(model_dump=lambda: {"type": "object"})
        else:
            self.inputSchema = input_schema


class FakeResult:
    def __init__(self, tools=None, content=None, structured=None):
        self.tools = tools or []
        self.content = content or []
        self.structuredContent = structured


class FakeSession:
    def __init__(self, *args, tools=None, call_result=None):
        self.tools = tools or []
        self.call_result = call_result or FakeResult(content=[SimpleNamespace(text="ok")])
        self.initialized = False
        self.closed = False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return FakeResult(tools=self.tools)

    async def call_tool(self, name, arguments):
        return self.call_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.closed = True


class FakeTransport:
    def __init__(self):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return "read", "write"

    async def __aexit__(self, *a):
        self.exited = True


@pytest.fixture
def fake_mcp(monkeypatch):
    """注入假 mcp 包（mcp / mcp.client / mcp.client.stdio）"""
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    stdio = types.ModuleType("mcp.client.stdio")
    stdio.StdioServerParameters = SimpleNamespace
    stdio.stdio_client = lambda params: FakeTransport()
    client.stdio = stdio
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio)
    return mcp


# ---------- connect_servers ----------


async def test_connect_empty(fake_mcp):
    bridge = MCPBridge()
    assert await bridge.connect_servers([]) == 0


async def test_connect_skips_unnamed(fake_mcp):
    bridge = MCPBridge()
    n = await bridge.connect_servers([SimpleNamespace(name="", command="x")])
    assert n == 0


async def test_connect_stdio(fake_mcp):
    bridge = MCPBridge()
    cfg = SimpleNamespace(name="srv", command="echo", args=["a"], env={"K": "V"}, url=None)
    n = await bridge._connect_one(cfg)
    assert n == 0  # FakeSession 无工具
    assert bridge.connected_servers == ["srv"]
    assert len(bridge._sessions) == 1


async def test_connect_servers_failure_tolerant(fake_mcp, monkeypatch):
    bridge = MCPBridge()

    async def boom(_cfg):
        raise RuntimeError("conn fail")

    monkeypatch.setattr(bridge, "_connect_one", boom)
    cfg = SimpleNamespace(name="srv", command="x", args=None, env=None, url=None)
    assert await bridge.connect_servers([cfg]) == 0


# ---------- register_tools ----------


async def test_register_tools_dedup(fake_mcp):
    registry = ToolRegistry()
    bridge = MCPBridge()
    tools = [
        FakeTool(name="a"),
        FakeTool(name="a"),  # 同名跳过
        FakeTool(name="b", input_schema={"type": "object", "properties": {}}),
    ]
    bridge._sessions = [("srv", FakeSession(tools=tools))]
    count = await bridge.register_tools(registry)
    assert count == 2
    assert registry.has("mcp_srv_a")
    assert registry.has("mcp_srv_b")
    names = [t["function"]["name"] for t in registry.get_openai_tools()]
    assert names == ["mcp_srv_a", "mcp_srv_b"]


async def test_register_tools_schema_dump(fake_mcp):
    registry = ToolRegistry()
    bridge = MCPBridge()
    bridge._sessions = [("srv", FakeSession(tools=[FakeTool(name="x", dump_schema=True)]))]
    await bridge.register_tools(registry)
    assert registry._tools["mcp_srv_x"]["parameters"] == {"type": "object"}


# ---------- _make_handler ----------


async def test_handler_text_join(fake_mcp):
    session = FakeSession(
        call_result=FakeResult(
            content=[SimpleNamespace(text="hello"), SimpleNamespace(text="world")]
        )
    )
    handler = MCPBridge._make_handler(session, "t1")
    assert await handler() == "hello\nworld"


async def test_handler_dict_text(fake_mcp):
    session = FakeSession(call_result=FakeResult(content=[{"text": "dict-text"}]))
    handler = MCPBridge._make_handler(session, "t1")
    assert await handler() == "dict-text"


async def test_handler_structured(fake_mcp):
    session = FakeSession(call_result=FakeResult(content=[], structured={"a": 1}))
    handler = MCPBridge._make_handler(session, "t1")
    assert await handler() == '{"a": 1}'


async def test_handler_empty(fake_mcp):
    session = FakeSession(call_result=FakeResult(content=[], structured=None))
    handler = MCPBridge._make_handler(session, "t1")
    assert await handler() == "工具 t1 返回空结果"


async def test_handler_call_failure(fake_mcp):
    class BoomSession(FakeSession):
        async def call_tool(self, name, arguments):
            raise RuntimeError("boom")

    handler = MCPBridge._make_handler(BoomSession(), "t1")
    out = await handler()
    assert "调用失败" in out


# ---------- close ----------


async def test_close_idempotent(fake_mcp):
    bridge = MCPBridge()
    s1, s2 = FakeSession(), FakeSession()
    t1, t2 = FakeTransport(), FakeTransport()
    bridge._sessions = [("a", s1), ("b", s2)]
    bridge._transports = [t1, t2]
    bridge._connected_servers = ["a", "b"]
    await bridge.close()
    assert s1.closed and s2.closed
    assert t1.exited and t2.exited
    assert bridge.connected_servers == []
    assert bridge._sessions == []
    assert bridge._transports == []
    # 幂等：再次关闭不抛异常
    await bridge.close()
