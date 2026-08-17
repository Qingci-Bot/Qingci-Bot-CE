"""类型化事件 LLM 工具化测试

验证：
- EventBuffer 记录类型化事件（notice/request，dict 兜底）与维度查询
- 容量上限（环形丢弃最旧）
- 工具注册（幂等）与 Function Calling 执行返回可读文本
- Dispatcher 分发事件后自动入缓冲（端到端：TestBot.send notice/request）
- 按群/按成员/hours/limit 过滤
"""

import pytest

from bot.llm.events_tools import EventBuffer, register_event_tools
from bot.plugin.events import (
    parse_notice_event,
    parse_request_event,
)
from bot.testing import make_notice_event, make_request_event


@pytest.fixture
def buffer() -> EventBuffer:
    return EventBuffer(capacity=10)


def _group_increase(**kw):
    return parse_notice_event(
        {
            "post_type": "notice",
            "notice_type": "group_increase",
            "user_id": 10001,
            "group_id": 20001,
            "operator_id": 30001,
            **kw,
        }
    )


# ---------- EventBuffer 记录与查询 ----------


def test_record_typed_notice(buffer):
    buffer.record(_group_increase())
    assert len(buffer) == 1
    entry = buffer.query()[0]
    assert entry["post_type"] == "notice"
    assert entry["group_id"] == 20001
    assert entry["user_id"] == 10001
    assert "成员入群" in entry["label"]
    assert "用户10001" in entry["detail"]


def test_record_request_event(buffer):
    evt = parse_request_event(
        {
            "post_type": "request",
            "request_type": "group",
            "user_id": 10002,
            "group_id": 20002,
            "comment": "求拉",
            "flag": "f1",
        }
    )
    buffer.record(evt)
    entry = buffer.query()[0]
    assert entry["post_type"] == "request"
    assert "加群请求" in entry["label"]
    assert "留言[求拉]" in entry["detail"]


def test_record_dict_fallback(buffer):
    """dict 兜底：非 SDK 事件也可记录"""
    buffer.record(
        {"post_type": "notice", "notice_type": "group_ban", "user_id": 10003, "group_id": 20003}
    )
    entry = buffer.query()[0]
    assert entry["label"] == "禁言"
    assert entry["user_id"] == 10003


def test_record_none_ignored(buffer):
    buffer.record(None)
    assert len(buffer) == 0


def test_capacity_ring_drops_oldest(buffer):
    for i in range(15):
        buffer.record(_group_increase(user_id=10000 + i))
    assert len(buffer) == 10  # 容量上限
    entries = buffer.query(limit=50)
    # 最旧 5 条被丢弃，最新 10 条保留（user_id 10005..10014）
    user_ids = [e["user_id"] for e in entries]
    assert min(user_ids) == 10005
    assert max(user_ids) == 10014


def test_query_filters(buffer):
    buffer.record(_group_increase(user_id=10001, group_id=20001))
    buffer.record(_group_increase(user_id=10002, group_id=20002))
    buffer.record(_group_increase(user_id=10003, group_id=20001))

    by_group = buffer.query(group_id=20001)
    assert len(by_group) == 2

    by_user = buffer.query(user_id=10002)
    assert len(by_user) == 1
    assert by_user[0]["group_id"] == 20002

    combined = buffer.query(group_id=20001, user_id=10001)
    assert len(combined) == 1


def test_query_hours_filter(buffer):
    buffer.record(_group_increase(user_id=10001))
    assert len(buffer.query(hours=24)) == 1
    assert len(buffer.query(hours=24 * 365)) == 1  # 大范围全部命中


# ---------- LLM 工具 ----------


def test_register_tools_idempotent(buffer):
    registry = _fake_registry()
    n1 = register_event_tools(registry, buffer)
    n2 = register_event_tools(registry, buffer)
    assert n1 == 2
    assert n2 == 0  # 幂等：已存在则跳过


def _fake_registry():
    """最小 ToolRegistry 替身（仅用 dict 模拟注册/查询/执行）"""

    class Reg:
        def __init__(self):
            self._tools = {}

        def has(self, name):
            return name in self._tools

        def register(self, name, description, parameters, handler):
            self._tools[name] = {
                "description": description,
                "parameters": parameters,
                "handler": handler,
            }

        def get_openai_tools(self):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": n,
                        "description": s["description"],
                        "parameters": s["parameters"],
                    },
                }
                for n, s in self._tools.items()
            ]

    return Reg()


async def test_get_group_events_tool(buffer):
    registry = _fake_registry()
    register_event_tools(registry, buffer)
    buffer.record(_group_increase(user_id=10001, group_id=20001))
    buffer.record(_group_increase(user_id=10002, group_id=20001))

    handler = registry._tools["get_group_events"]["handler"]
    text = handler(group_id=20001)
    assert "成员入群" in text
    assert "群20001" in text
    assert "用户10001" in text and "用户10002" in text

    empty = handler(group_id=99999)
    assert "无相关事件记录" in empty


async def test_get_member_events_tool(buffer):
    registry = _fake_registry()
    register_event_tools(registry, buffer)
    buffer.record(_group_increase(user_id=10001, group_id=20001))
    buffer.record(_group_increase(user_id=10002, group_id=20001))

    handler = registry._tools["get_member_events"]["handler"]
    text = handler(user_id=10001)
    assert "用户10001" in text
    assert "用户10002" not in text

    # group_id 限定
    text2 = handler(user_id=10001, group_id=99999)
    assert "无相关事件记录" in text2


async def test_tool_validation(buffer):
    registry = _fake_registry()
    register_event_tools(registry, buffer)
    gh = registry._tools["get_group_events"]["handler"]
    mh = registry._tools["get_member_events"]["handler"]

    assert "参数错误" in gh(group_id=-1)
    assert "参数错误" in mh(user_id=0)
    # limit 钳制
    for i in range(5):
        buffer.record(_group_increase(user_id=10000 + i, group_id=20001))
    assert len(gh(group_id=20001, limit=2).strip().splitlines()) == 2


# ---------- 端到端：分发自动入缓冲 ----------


async def test_dispatcher_records_events(bot):
    """TestBot.send(notice/request) 后事件自动进入缓冲"""
    await bot.send(make_notice_event("group_increase", group_id=20001, user_id=10001))
    await bot.send(make_notice_event("group_ban", group_id=20001, user_id=10002, duration=60))
    await bot.send(make_request_event("friend", user_id=10003, comment="hi"))

    assert len(bot.event_buffer) == 3

    # 按群查询
    entries = bot.event_buffer.query(group_id=20001)
    assert len(entries) == 2

    # 工具可从注册表执行
    text = await bot.tool_registry.execute("get_group_events", {"group_id": 20001})
    assert "成员入群" in text
    assert "禁言" in text

    text_member = await bot.tool_registry.execute("get_member_events", {"user_id": 10002})
    assert "禁言" in text_member


async def test_tool_registry_integration(bot):
    """真实 ToolRegistry：工具定义导出 + execute"""
    assert bot.tool_registry.has("get_group_events")
    assert bot.tool_registry.has("get_member_events")

    tools = bot.tool_registry.get_openai_tools()
    names = [t["function"]["name"] for t in tools]
    assert "get_group_events" in names

    # 无事件时返回空提示
    text = await bot.tool_registry.execute("get_group_events", {"group_id": 1})
    assert "无相关事件记录" in text
