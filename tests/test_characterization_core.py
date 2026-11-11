"""P0 行为锁定：0% / 低覆盖核心模块的特征测试

只断言「现在实际发生的行为」，不预设理想行为、不改动产品代码。
后续重构（组合根改造、调度器调整、事件类型化）以这些用例为回归基准。

覆盖：
- bot/filter.py：词库加载、注释/空行、正则转义、打码、reload、读取失败降级
- bot/core/scheduler.py：延迟注册去重、owner 前缀、异常隔离、幂等 shutdown
- bot/core/composition.py：装配产物与 DI 注册面、可选组件开关、平台适配器注册
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

import bot.core.composition as comp
from bot.core.di import DIContainer
from bot.core.scheduler import BotScheduler
from bot.filter import SensitiveFilter

# ---------- bot/filter.py ----------


def test_missing_wordbook_degrades_to_noop(tmp_path):
    f = SensitiveFilter(tmp_path / "absent.txt")
    assert f.words == []
    assert f.check("任何文本") is None
    assert f.mask("任何文本") == "任何文本"


def test_wordbook_ignores_comments_and_blank_lines(tmp_path):
    p = tmp_path / "words.txt"
    p.write_text("# 注释\n\n  坏词  \n  # 也是注释\n好词\n", encoding="utf-8")
    f = SensitiveFilter(p)
    assert f.words == ["坏词", "好词"]
    assert f.check("这是坏词。") == "坏词"
    assert f.mask("这是坏词。") == "这是**。"
    assert f.mask("坏词", char="#") == "##"
    assert f.check("完全正常") is None


def test_regex_metacharacters_are_escaped(tmp_path):
    p = tmp_path / "words.txt"
    p.write_text("a.b\n[x]\n", encoding="utf-8")
    f = SensitiveFilter(p)
    assert f.check("axb") is None
    assert f.check("a.b") == "a.b"
    assert f.check("[x]") == "[x]"
    assert f.mask("a.b") == "***"


def test_reload_picks_up_wordbook_changes(tmp_path):
    p = tmp_path / "words.txt"
    p.write_text("旧词\n", encoding="utf-8")
    f = SensitiveFilter(p)
    assert f.check("旧词") == "旧词"
    p.write_text("新词\n", encoding="utf-8")
    f.reload()
    assert f.words == ["新词"]
    assert f.check("旧词") is None
    assert f.check("新词") == "新词"


def test_unreadable_wordbook_falls_back_to_empty(tmp_path, monkeypatch):
    p = tmp_path / "words.txt"
    p.write_text("坏词\n", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("disk error")

    with monkeypatch.context() as m:
        m.setattr(Path, "read_text", boom)
        f = SensitiveFilter(p)
    assert f.words == []
    assert f.check("坏词") is None


# ---------- bot/core/scheduler.py ----------


def test_deferred_jobs_are_prefixed_deduplicated_and_removable():
    s = BotScheduler()
    assert s.running is False

    def job():
        return None

    assert s.add_job(job, "interval", "tick", "demo", seconds=60) is None
    s.add_job(job, "interval", "tick", "demo", seconds=30)  # 同 full_id 覆盖
    assert [j.id for j in s.get_jobs()] == ["demo:tick"]
    assert all(j.next_run_time is None for j in s.get_jobs())

    s.add_job(job, "interval", "solo", "", seconds=1)  # 空 owner 不加前缀
    assert sorted(j.id for j in s.get_jobs()) == ["demo:tick", "solo"]
    s.remove_jobs_by_owner("demo")
    assert [j.id for j in s.get_jobs()] == ["solo"]


def test_start_flushes_deferred_jobs_and_is_idempotent(monkeypatch):
    class FakeScheduler:
        def __init__(self):
            self.running = False
            self.jobs = []

        def start(self):
            self.running = True

        def add_job(self, func, trigger, **kwargs):
            self.jobs.append(kwargs["id"])

        def get_jobs(self):
            return []

    fake = FakeScheduler()
    s = BotScheduler()
    monkeypatch.setattr(s, "_scheduler", fake)
    s.add_job(lambda: None, "interval", "a", "p", seconds=1)
    s.add_job(lambda: None, "interval", "b", "p", seconds=1)
    s.start()
    assert sorted(fake.jobs) == ["p:a", "p:b"]
    assert s._deferred == {}
    s.start()
    assert len(fake.jobs) == 2


async def test_shutdown_is_noop_when_never_started():
    s = BotScheduler()
    await s.shutdown()
    assert s.running is False


def test_wrapped_sync_job_never_raises_and_preserves_metadata():
    def job(x):
        return x * 2

    wrapped = BotScheduler._wrap_func(job, "demo:double")
    assert wrapped(3) == 6
    assert wrapped.__name__ == "job"

    def boom():
        raise RuntimeError("boom")

    assert BotScheduler._wrap_func(boom, "demo:boom")() is None


async def test_wrapped_async_job_never_raises():
    async def boom():
        raise RuntimeError("boom")

    assert await BotScheduler._wrap_func(boom, "demo:aboom")() is None


# ---------- bot/core/composition.py ----------


class _Stub:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _LlmStub(_Stub):
    def set_tool_registry(self, registry):
        self.registry = registry


class _FakePlatform(_Stub):
    name = "telegram"
    display_name = "Telegram"


def _cfg(*, rag=False, rate_limit=False, telegram=False):
    return types.SimpleNamespace(
        onebot=types.SimpleNamespace(host="127.0.0.1", port=3001, access_token="", enabled=True),
        platforms=types.SimpleNamespace(
            telegram=types.SimpleNamespace(enabled=telegram),
            onebot12=types.SimpleNamespace(enabled=False),
        ),
        llm=types.SimpleNamespace(api_url="", api_key=""),
        session_summary=None,
        log=types.SimpleNamespace(usage_tracking=False),
        render=None,
        rag=types.SimpleNamespace(
            enabled=rag,
            knowledge_dir="kb",
            mode="keyword",
            chunk_size=500,
            chunk_overlap=50,
            top_k=3,
            embedding_model="",
            embedding_api_url="",
            embedding_api_key="",
            collection_name="kb",
        ),
        filter=types.SimpleNamespace(words_file="words.txt"),
        rate_limit=types.SimpleNamespace(enabled=rate_limit, daily_limit=10, cooldown_seconds=1),
    )


@pytest.fixture
def patched_composition(monkeypatch, tmp_path):
    """把重组件换成无副作用替身，只保留真实 DI 容器与敏感词过滤器"""
    for name in (
        "Database",
        "OneBotConnection",
        "HtmlRenderer",
        "PluginManager",
        "MessageDispatcher",
        "ToolRegistry",
        "EventBuffer",
        "SessionStateManager",
        "EventBus",
        "BotScheduler",
        "RateLimiter",
        "KnowledgeStore",
    ):
        monkeypatch.setattr(comp, name, _Stub)
    monkeypatch.setattr(comp, "LLMManager", _LlmStub)
    monkeypatch.setattr(comp, "make_platform", lambda cfg: _FakePlatform(cfg))
    builtin: list = []
    event_tools: list = []
    monkeypatch.setattr(comp, "register_builtin_tools", lambda reg: builtin.append(reg))
    monkeypatch.setattr(
        comp, "register_event_tools", lambda reg, buf: event_tools.append((reg, buf))
    )
    monkeypatch.setattr(comp, "data_root", lambda: tmp_path)
    return builtin, event_tools


def test_assemble_bot_builds_and_registers_all_core_services(patched_composition, tmp_path):
    builtin, event_tools = patched_composition
    cfg = _cfg()
    bot = types.SimpleNamespace(config=cfg)

    comp.assemble_bot(bot)

    assert bot.platforms == {"onebot": bot.connection}
    for attr in (
        "db",
        "connection",
        "dispatcher",
        "llm",
        "html_renderer",
        "plugin_manager",
        "di",
        "session_state",
        "event_bus",
        "scheduler",
        "tool_registry",
        "event_buffer",
        "sensitive_filter",
        "rate_limiter",
        "knowledge_store",
    ):
        assert hasattr(bot, attr), attr
    assert bot.rate_limiter is None
    assert bot.knowledge_store is None
    assert isinstance(bot.di, DIContainer)
    assert builtin == [bot.tool_registry]
    assert event_tools == [(bot.tool_registry, bot.event_buffer)]
    assert bot.llm.registry is bot.tool_registry
    assert isinstance(bot.sensitive_filter, SensitiveFilter)
    assert bot.sensitive_filter.words == []

    di = bot.di
    assert di.resolve_sync(comp.ConfigManager) is cfg
    for cls_name in (
        "Database",
        "OneBotConnection",
        "LLMManager",
        "HtmlRenderer",
        "PluginManager",
        "MessageDispatcher",
        "SessionStateManager",
        "EventBus",
        "DIContainer",
    ):
        assert di.resolve_sync(getattr(comp, cls_name)) is not None, cls_name


def test_assemble_bot_respects_optional_component_switches(patched_composition, tmp_path):
    cfg = _cfg(rag=True, rate_limit=True, telegram=True)
    bot = types.SimpleNamespace(config=cfg)

    comp.assemble_bot(bot)

    assert isinstance(bot.rate_limiter, _Stub)
    assert bot.rate_limiter.kwargs == {"daily_limit": 10, "cooldown_seconds": 1}
    assert isinstance(bot.knowledge_store, _Stub)
    assert bot.knowledge_store.kwargs["root"] == tmp_path / "kb"
    assert bot.knowledge_store.kwargs["embedding_api_url"] == ""
    assert "telegram" in bot.platforms
    assert bot.platforms["telegram"].name == "telegram"


def test_build_bot_is_thin_entrypoint(monkeypatch):
    import bot.core.bot as bot_mod

    seen = {}

    class FakeQingciBot:
        def __init__(self, config_path=None):
            seen["path"] = config_path

    monkeypatch.setattr(bot_mod, "QingciBot", FakeQingciBot)
    result = comp.build_bot("some.yaml")
    assert isinstance(result, FakeQingciBot)
    assert seen["path"] == "some.yaml"
