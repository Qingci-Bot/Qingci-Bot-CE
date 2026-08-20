"""LLM 管理器测试

使用 FakeAdapter / FakeDB 隔离网络与数据库，验证：
- 会话管理：session key、DB 懒加载、clear_session（单/全/参数错误）
- Token 估算：字符降级 + 缓存
- 历史裁剪：条数裁剪、摘要压缩（开关/阈值/持久化）
- chat：正常、失败回滚、空回复回滚、Function Calling（chat_with_tools）
- chat_stream：正常流式、空流回滚
- reload / close / check_availability
"""

from types import SimpleNamespace

import pytest

from bot.config import LLMConfig, SessionSummaryConfig
from bot.core.tasks import await_pending_tasks
from bot.llm.adapter import ChatResult, LLMAdapter
from bot.llm.manager import LLMManager
from bot.llm.tools import ToolRegistry


class FakeAdapter(LLMAdapter):
    """可控回复/错误/工具调用序列的假适配器"""

    def __init__(
        self,
        reply: str = "ok",
        usage: dict | None = None,
        tool_calls=None,
        tool_calls_sequence: list | None = None,
        fail: bool = False,
        stream_chunks: list[str] | None = None,
        availability: bool = True,
    ):
        self.reply = reply
        self.usage = usage
        self.tool_calls = tool_calls
        self.tool_calls_sequence = tool_calls_sequence
        self.fail = fail
        self.stream_chunks = stream_chunks if stream_chunks is not None else ["你", "好"]
        self.availability = availability
        self.calls: list[dict] = []
        self.last_error = ""
        self._tc_call = 0

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def chat_detail(
        self,
        messages,
        system_prompt=None,
        max_tokens=2048,
        temperature=0.7,
        tools=None,
        images=None,
        **kwargs,
    ):
        self.calls.append(
            {"messages": messages, "tools": tools, "images": images, "system_prompt": system_prompt}
        )
        if self.fail:
            raise RuntimeError("llm boom")
        if self.tool_calls_sequence is not None:
            idx = min(self._tc_call, len(self.tool_calls_sequence) - 1)
            self._tc_call += 1
            tc = self.tool_calls_sequence[idx]
        else:
            tc = self.tool_calls
        return ChatResult(content=self.reply, usage=self.usage, tool_calls=tc)

    async def chat(
        self,
        messages,
        system_prompt=None,
        max_tokens=2048,
        temperature=0.7,
        tools=None,
        images=None,
        **kwargs,
    ):
        r = await self.chat_detail(
            messages, system_prompt, max_tokens, temperature, tools, images, **kwargs
        )
        return r.content

    async def chat_stream(
        self, messages, system_prompt=None, max_tokens=2048, temperature=0.7, **kwargs
    ):
        for c in self.stream_chunks:
            yield c

    async def check_availability(self):
        return self.availability

    async def close(self):
        pass


class FakeDB:
    """内存版 Database 替身（仅实现 manager 用到的接口）"""

    def __init__(self):
        self.sessions: list[tuple[str, str, str]] = []  # (key, role, content)
        self.usage: list[dict] = []

    async def get_sessions(self, key, limit=0):
        rows = [{"role": r, "content": c} for k, r, c in self.sessions if k == key]
        return rows[-limit:] if limit else rows

    async def save_session(self, key, role, content):
        self.sessions.append((key, role, content))

    async def clear_sessions(self, key=None):
        if key is None:
            self.sessions.clear()
        else:
            self.sessions = [s for s in self.sessions if s[0] != key]

    async def trim_sessions(self, key, max_msgs):
        rows = [s for s in self.sessions if s[0] == key]
        if len(rows) > max_msgs:
            self.sessions = [s for s in self.sessions if s[0] != key] + rows[-max_msgs:]

    async def delete_last_session(self, key, role):
        for i in range(len(self.sessions) - 1, -1, -1):
            if self.sessions[i][0] == key and self.sessions[i][1] == role:
                self.sessions.pop(i)
                break

    async def save_usage(self, **kw):
        self.usage.append(kw)


@pytest.fixture
def config() -> LLMConfig:
    return LLMConfig(provider="openai", model="gpt-4o-mini", api_key="k")


@pytest.fixture
def manager(config) -> LLMManager:
    m = LLMManager(config=config, db=None, usage_tracking=True)
    m._adapter = FakeAdapter()
    return m


@pytest.fixture(autouse=True)
async def _flush_background_tasks():
    """每个测试后回收 fire-and-forget 的后台任务，避免泄漏"""
    yield
    await await_pending_tasks(0.5)


# ---------- 会话 key ----------


def test_session_key(manager):
    assert manager._session_key("private", 0, 100) == "private:100"
    assert manager._session_key("group", 200, 100) == "group:200:100"
    assert manager._session_key("other", 200, 100) == "group:200:100"


# ---------- chat ----------


async def test_chat_ok(manager):
    db = FakeDB()
    manager._db = db
    reply = await manager.chat("你好", message_type="private", user_id=1, user_name="Alice")
    assert reply == "ok"
    roles = [m["role"] for m in manager._sessions["private:1"]]
    assert roles == ["user", "assistant"]
    assert manager._sessions["private:1"][-1]["content"] == "ok"
    assert ("private:1", "user", "你好") in db.sessions
    assert ("private:1", "assistant", "ok") in db.sessions


async def test_chat_usage_tracking(manager):
    db = FakeDB()
    manager._db = db
    manager._adapter = FakeAdapter(usage={"prompt_tokens": 10, "completion_tokens": 5})
    await manager.chat("hi", message_type="private", user_id=1)
    assert db.usage and db.usage[-1]["prompt_tokens"] == 10
    assert db.usage[-1]["completion_tokens"] == 5
    assert db.usage[-1]["source"] == "chat"
    assert db.usage[-1]["user_id"] == 1


async def test_chat_usage_disabled(manager):
    db = FakeDB()
    manager._db = db
    manager._usage_tracking = False
    manager._adapter = FakeAdapter(usage={"prompt_tokens": 1, "completion_tokens": 1})
    await manager.chat("hi", message_type="private", user_id=1)
    assert db.usage == []


async def test_chat_failure_rolls_back(manager):
    db = FakeDB()
    manager._db = db
    manager._adapter = FakeAdapter(fail=True)
    reply = await manager.chat("hi", message_type="private", user_id=1)
    assert reply is None
    assert manager._sessions.get("private:1") in (None, [])
    assert db.sessions == []


async def test_chat_empty_reply_rolls_back(manager):
    db = FakeDB()
    manager._db = db
    manager._adapter = FakeAdapter(reply="")
    reply = await manager.chat("hi", message_type="private", user_id=1)
    assert reply is None
    assert manager._sessions.get("private:1") in (None, [])
    assert db.sessions == []


async def test_chat_lazy_loads_from_db(manager):
    db = FakeDB()
    db.sessions = [("private:1", "user", "old1"), ("private:1", "assistant", "old2")]
    manager._db = db
    await manager.chat("new", message_type="private", user_id=1)
    roles = [m["role"] for m in manager._sessions["private:1"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert manager._sessions["private:1"][-1]["content"] == "ok"


async def test_chat_system_prompt_override(manager):
    await manager.chat("hi", message_type="private", user_id=1, system_prompt="覆盖提示词")
    assert manager._adapter.calls[-1]["system_prompt"] == "覆盖提示词"


# ---------- clear_session ----------


async def test_clear_session_requires_user_id(manager):
    with pytest.raises(ValueError):
        await manager.clear_session(message_type="private")


async def test_clear_session_one(manager):
    await manager.chat("a", message_type="private", user_id=1)
    await manager.chat("b", message_type="private", user_id=2)
    await manager.clear_session(message_type="private", user_id=1)
    assert "private:1" not in manager._sessions
    assert "private:2" in manager._sessions


async def test_clear_session_all(manager):
    db = FakeDB()
    manager._db = db
    await manager.chat("a", message_type="private", user_id=1)
    await manager.chat("a", message_type="group", group_id=9, user_id=2)
    await manager.clear_session()
    assert manager._sessions == {}
    assert db.sessions == []


# ---------- token 估算 ----------


def test_estimate_tokens_fallback_and_cache(manager, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no tokenizer")

    import litellm

    monkeypatch.setattr(litellm, "token_counter", boom)
    n = manager._estimate_message_tokens({"role": "user", "content": "你好"})
    assert n == 8  # 2 字符 × 2 + 4 固定开销
    # 缓存命中，不再调用 token_counter
    assert manager._estimate_message_tokens({"role": "user", "content": "你好"}) == n


# ---------- 历史裁剪 ----------


async def test_trim_history_by_count(manager):
    manager._config.max_history = 2  # 上限 4 条
    key = "private:1"
    manager._sessions[key] = [{"role": "user", "content": f"m{i}"} for i in range(6)]
    await manager._trim_history(key)
    assert len(manager._sessions[key]) == 4
    assert manager._sessions[key][-1]["content"] == "m5"


async def test_summarize_history_triggered(manager):
    manager._config.enable_summary = True
    manager._summary_config = SessionSummaryConfig(
        enabled=True, keep_recent_turns=1, max_messages=8
    )
    key = "private:1"
    flat: list[dict] = []
    for i in range(5):
        flat += [
            {"role": "user", "content": f"msg{i}"},
            {"role": "assistant", "content": f"ans{i}"},
        ]
    manager._sessions[key] = flat
    manager._adapter = FakeAdapter(reply="这是摘要")
    ok = await manager._summarize_history(key, flat)
    assert ok is True
    hist = manager._sessions[key]
    assert hist[0]["role"] == "system"
    assert "摘要" in hist[0]["content"]
    assert len(hist) >= 3  # summary + 最近 1 轮原文


async def test_summarize_history_persists_db(manager):
    db = FakeDB()
    manager._db = db
    manager._config.enable_summary = True
    manager._summary_config = SessionSummaryConfig(
        enabled=True, keep_recent_turns=1, max_messages=8
    )
    key = "private:1"
    flat: list[dict] = []
    for i in range(5):
        flat += [
            {"role": "user", "content": f"msg{i}"},
            {"role": "assistant", "content": f"ans{i}"},
        ]
    manager._adapter = FakeAdapter(
        reply="这是摘要", usage={"prompt_tokens": 5, "completion_tokens": 3}
    )
    ok = await manager._summarize_history(key, flat)
    assert ok is True
    await await_pending_tasks(0.5)  # 等待 fire-and-forget 的用量入库
    rows = [s for s in db.sessions if s[0] == key]
    assert any("摘要" in c for _, _, c in rows)
    assert db.usage and db.usage[-1]["source"] == "summary"


async def test_summarize_disabled_returns_false(manager):
    key = "private:1"
    manager._sessions[key] = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y"},
    ]
    assert await manager._summarize_history(key, manager._sessions[key]) is False


# ---------- Function Calling ----------


def _tool_call(name: str = "my_tool", args: str = "{}", id: str = "t1"):
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=args))


async def test_chat_with_tools_loop(manager):
    registry = ToolRegistry()
    calls: list[dict] = []

    async def _tool(**kw):
        calls.append(kw)
        return "done"

    registry.register(
        "my_tool", "测试工具", {"type": "object", "properties": {"x": {"type": "string"}}}, _tool
    )
    manager.set_tool_registry(registry)
    manager._tools_support_cache = True
    tc = _tool_call(args='{"x": 1}')
    manager._adapter = FakeAdapter(reply="final", tool_calls_sequence=[[tc], None])
    reply, usage = await manager.chat_with_tools([{"role": "user", "content": "hi"}])
    assert reply == "final"
    assert calls == [{"x": 1}]  # json.loads('{"x": 1}') → {"x": 1}
    # 工具执行结果回传（工作副本含 tool 消息）
    roles = [m["role"] for m in manager._adapter.calls[-1]["messages"]]
    assert "assistant" in roles and "tool" in roles


async def test_chat_with_tools_bad_args(manager):
    registry = ToolRegistry()
    registry.register("t", "desc", {"type": "object", "properties": {}}, lambda: "x")
    manager.set_tool_registry(registry)
    manager._tools_support_cache = True
    manager._adapter = FakeAdapter(
        reply="r", tool_calls_sequence=[[_tool_call(args="not json")], None]
    )
    reply, _ = await manager.chat_with_tools([{"role": "user", "content": "hi"}])
    assert reply == "r"


async def test_chat_with_tools_max_rounds(manager):
    registry = ToolRegistry()

    async def _tool(**kw):
        return "again"

    registry.register("t", "desc", {"type": "object", "properties": {}}, _tool)
    manager.set_tool_registry(registry)
    manager._tools_support_cache = True
    manager._config.max_tool_rounds = 2
    manager._adapter = FakeAdapter(
        reply="done", tool_calls_sequence=[[_tool_call()], [_tool_call()], [_tool_call()]]
    )
    reply, _ = await manager.chat_with_tools([{"role": "user", "content": "hi"}])
    assert reply == "done"
    # 2 轮 + 1 次强制收尾
    assert len(manager._adapter.calls) == 3
    assert manager._adapter.calls[-1]["tools"] is None


async def test_chat_with_tools_usage_accumulated(manager):
    registry = ToolRegistry()
    registry.register("t", "desc", {"type": "object", "properties": {}}, lambda: "x")
    manager.set_tool_registry(registry)
    manager._tools_support_cache = True
    manager._adapter = FakeAdapter(
        reply="r",
        usage={"prompt_tokens": 5, "completion_tokens": 3},
        tool_calls_sequence=[[_tool_call()], None],
    )
    _, usage = await manager.chat_with_tools([{"role": "user", "content": "hi"}])
    assert usage == {"prompt_tokens": 10, "completion_tokens": 6}  # 两轮累加


# ---------- chat_stream ----------


async def test_chat_stream_ok(manager):
    db = FakeDB()
    manager._db = db
    chunks = []
    async for c in manager.chat_stream("你好", message_type="private", user_id=1):
        chunks.append(c)
    assert "".join(chunks) == "你好"
    roles = [m["role"] for m in manager._sessions["private:1"]]
    assert roles == ["user", "assistant"]
    assert ("private:1", "assistant", "你好") in db.sessions


async def test_chat_stream_empty_rolls_back(manager):
    db = FakeDB()
    manager._db = db
    manager._adapter = FakeAdapter(stream_chunks=[])
    chunks = []
    async for c in manager.chat_stream("hi", message_type="private", user_id=1):
        chunks.append(c)
    assert chunks == []
    assert manager._sessions.get("private:1") in (None, [])
    assert db.sessions == []


# ---------- reload / close / check_availability ----------


async def test_reload_swaps_config(manager):
    manager._sessions["private:1"] = [{"role": "user", "content": "x"}]
    manager._loaded_sessions.add("private:1")
    new = LLMConfig(provider="deepseek", model="deepseek-chat", api_key="k2")
    await manager.reload(new)
    assert manager._config is new
    # reload 不再清空内存会话：仅调整参数不应重置用户的对话上下文
    assert manager._sessions == {"private:1": [{"role": "user", "content": "x"}]}
    assert manager._loaded_sessions == {"private:1"}
    assert manager.adapter.model == "deepseek-chat"


async def test_close(manager):
    await manager.close()
    assert manager._adapter is None
    assert manager._sessions == {}


async def test_check_availability(manager):
    manager._adapter = FakeAdapter(availability=False)
    manager._adapter.last_error = "bad"
    assert await manager.check_availability() is False
    assert manager.last_error == "bad"
