# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""AppScope 契约：进程级引用集中管理与测试隔离

验证各模块入口（get_bot / get_engine / _get_litellm / runlog handler）都读写
同一个作用域，以及 set_scope() / reset_scope() 的隔离语义。
"""

import logging
import sys
import types
from types import SimpleNamespace

import pytest

from bot.core.scope import AppScope, get_scope, reset_scope, set_scope


@pytest.fixture(autouse=True)
def isolate_scope():
    """每个测试用全新作用域，结束后恢复原默认作用域（不污染其它测试）"""
    original = get_scope()
    set_scope(AppScope())
    yield
    set_scope(original)


def test_scope_starts_empty():
    scope = get_scope()
    assert (
        scope.bot_container,
        scope.engine,
        scope.session_factory,
        scope.litellm,
        scope.runlog_handler,
    ) == (None, None, None, None, None)


def test_reset_clears_all_references():
    scope = get_scope()
    for field in (
        "bot_container",
        "engine",
        "session_factory",
        "litellm",
        "runlog_handler",
    ):
        setattr(scope, field, object())
    reset_scope()
    assert (
        scope.bot_container,
        scope.engine,
        scope.session_factory,
        scope.litellm,
        scope.runlog_handler,
    ) == (None, None, None, None, None)


def test_set_scope_returns_new_instance():
    first = get_scope()
    second = set_scope()
    assert second is not first
    assert get_scope() is second
    custom = AppScope()
    assert set_scope(custom) is custom


def test_get_bot_uses_scope():
    from bot.core import bot as bot_mod
    from bot.core.di import DIContainer

    with pytest.raises(RuntimeError, match="Bot 未初始化"):
        bot_mod.get_bot()

    container = DIContainer()
    fake_bot = SimpleNamespace(di=container)
    container.register_sync(bot_mod.QingciBot, fake_bot)
    bot_mod.set_bot(fake_bot)

    assert get_scope().bot_container is container
    assert bot_mod.get_bot() is fake_bot

    bot_mod.clear_bot()
    assert get_scope().bot_container is None
    with pytest.raises(RuntimeError, match="Bot 未初始化"):
        bot_mod.get_bot()


async def test_engine_lifecycle_uses_scope(tmp_path, monkeypatch):
    import bot.db.engine as engine_mod

    monkeypatch.setattr(engine_mod, "data_root", lambda: tmp_path)
    engine = engine_mod.get_engine()
    assert engine is not None
    assert get_scope().engine is engine
    assert engine_mod.get_engine() is engine

    factory = engine_mod.get_session_factory()
    assert get_scope().session_factory is factory

    await engine_mod.dispose_engine()
    assert get_scope().engine is None
    assert get_scope().session_factory is None


def test_litellm_cache_uses_scope(monkeypatch):
    fake = types.ModuleType("litellm")
    fake.suppress_debug_info = False
    fake.set_verbose = lambda _v: None
    monkeypatch.setitem(sys.modules, "litellm", fake)

    from bot.llm.litellm_adapter import _get_litellm

    assert _get_litellm() is fake
    assert get_scope().litellm is fake
    assert _get_litellm() is fake


def test_runlog_handler_uses_scope():
    from bot import logformat

    root = logging.getLogger()
    logformat._attach_runlog_handler(root)
    handler = get_scope().runlog_handler
    assert handler is not None and handler in root.handlers

    logformat._detach_runlog_handler(root)
    assert handler not in root.handlers
