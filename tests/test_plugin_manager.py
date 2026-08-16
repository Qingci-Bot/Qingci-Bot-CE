"""PluginManager 插件管理器测试：加载/重载/依赖/循环依赖/多类拒绝"""

import pytest


async def test_load_external_simple(bot):
    ok = await bot.plugin_manager.load_external("plugin_pkg.simple_plugin", bot)
    assert ok is True

    plugin = bot.plugin_manager.get("simple")
    assert plugin is not None
    assert plugin.name == "simple"
    assert plugin.version == "1.0.0"

    # 插件内注册 2 个 + 模块级装饰器 1 个
    assert len(plugin.matchers) == 3
    # matcher 的 owner 已被补齐
    assert all(m.owner == "simple" for m in plugin.matchers)


async def test_load_missing_module_fails(bot):
    ok = await bot.plugin_manager.load_external("plugin_pkg.no_such_module", bot)
    assert ok is False


async def test_load_multi_class_rejected(bot):
    """单模块定义多个 PluginBase 子类必须拒绝"""
    ok = await bot.plugin_manager.load_external("plugin_pkg.multi_class_plugin", bot)
    assert ok is False
    assert bot.plugin_manager.get("first") is None
    assert bot.plugin_manager.get("second") is None


async def test_load_circular_dependency_rejected(bot):
    """循环依赖（A 依赖 B 且 B 正在加载 A）必须抛 ValueError"""
    from types import SimpleNamespace

    pm = bot.plugin_manager
    plugin_a = SimpleNamespace(name="circular_a", require=["circular_b"])
    # loading 集合已含 "circular_b"（模拟 B 正在加载并依赖 A）：
    # _ensure_dependencies 检测到循环依赖
    with pytest.raises(ValueError, match="循环依赖"):
        await pm._ensure_dependencies(plugin_a, bot, {"circular_b"})


async def test_dependency_already_registered(bot):
    """require 依赖已注册时应跳过自动加载"""
    # 先加载被依赖插件（name="dep"）
    ok = await bot.plugin_manager.load_external("plugin_pkg.dep", bot)
    assert ok is True

    ok = await bot.plugin_manager.load_external("plugin_pkg.dep_plugin", bot)
    assert ok is True
    consumer = bot.plugin_manager.get("dep_consumer")
    assert consumer is not None
    assert consumer._dep is bot.plugin_manager.get("dep")


async def test_dependency_missing_rejected(bot):
    """require 依赖既未注册也不存在（非内置）时必须加载失败"""
    ok = await bot.plugin_manager.load_external("plugin_pkg.ghost_dep_plugin", bot)
    assert ok is False
    assert bot.plugin_manager.get("ghost_consumer") is None


async def test_reload_keeps_plugin(bot):
    """重载后插件仍注册且 matchers 不丢失"""
    pm = bot.plugin_manager
    ok = await pm.load_external("plugin_pkg.simple_plugin", bot)
    assert ok is True
    before = [(m.priority, m.owner) for m in pm.all_matchers()]

    await pm.reload("simple", bot)
    plugin = pm.get("simple")
    assert plugin is not None
    assert len(plugin.matchers) == 3
    after = [(m.priority, m.owner) for m in pm.all_matchers()]
    assert after == before


async def test_reload_missing_plugin_noop(bot):
    """重载不存在的插件应静默跳过"""
    await bot.plugin_manager.reload("ghost", bot)  # 不抛异常


async def test_unload(bot):
    pm = bot.plugin_manager
    await pm.load_external("plugin_pkg.simple_plugin", bot)
    assert pm.get("simple") is not None

    await pm.unload("simple")
    assert pm.get("simple") is None
    assert pm.all_matchers() == []


async def test_shutdown_unloads_all(bot):
    pm = bot.plugin_manager
    await pm.load_external("plugin_pkg.simple_plugin", bot)
    await pm.load_external("plugin_pkg.dep", bot)
    await pm.load_external("plugin_pkg.dep_plugin", bot)
    assert len(pm.plugins) == 3  # simple + dep + dep_consumer

    await pm.shutdown()
    assert pm.plugins == {}


async def test_matchers_sorted_by_priority(bot):
    """all_matchers 应按 priority 升序排列"""
    pm = bot.plugin_manager
    await pm.load_external("plugin_pkg.simple_plugin", bot)
    plugin = pm.get("simple")
    # 追加高优先级（priority=0）与低优先级（priority=100）的 matcher
    plugin.matchers.append(_make_matcher("p0", 0))
    plugin.matchers.append(_make_matcher("p100", 100))
    pm._invalidate_matchers_cache()

    priorities = [m.priority for m in pm.all_matchers()]
    assert priorities == sorted(priorities)


async def test_matchers_event_type_inverted_index(bot):
    """事件类型倒排索引：all_matchers(post_type) 只返回该类型的 Matcher"""
    pm = bot.plugin_manager
    await pm.load_external("plugin_pkg.simple_plugin", bot)
    plugin = pm.get("simple")

    msg = _make_matcher("msg", 1)
    msg.event_type = "message"
    notice = _make_matcher("notice", 2)
    notice.event_type = "notice"
    request = _make_matcher("request", 3)
    request.event_type = "request"
    plugin.matchers.extend([msg, notice, request])
    pm._invalidate_matchers_cache()

    # 全量包含所有类型
    all_types = {m.event_type for m in pm.all_matchers()}
    assert "message" in all_types and "notice" in all_types and "request" in all_types

    # 按类型过滤：只返回对应类型
    assert {m.event_type for m in pm.all_matchers("notice")} == {"notice"}
    assert {m.event_type for m in pm.all_matchers("request")} == {"request"}

    # 未注册的事件类型返回空列表
    assert pm.all_matchers("meta_event") == []

    # 各类型子列表保持优先级升序
    msg_events = pm.all_matchers("message")
    priorities = [m.priority for m in msg_events]
    assert priorities == sorted(priorities)


def _make_matcher(owner: str, priority: int):
    from bot.plugin.matcher import Matcher

    async def handler(ctx):
        return None

    m = Matcher(handler=handler, priority=priority, block=False)
    m.owner = owner
    return m


async def test_load_builtin_scans_directory(bot):
    """源码模式：load_builtin 通过 pkgutil 扫描 builtin 包目录加载全部内置插件"""
    pm = bot.plugin_manager
    await pm.load_builtin(bot)

    names = {p.name for p in pm.plugins.values()}
    assert names == {"admin", "chat", "help", "imagegen", "knowledge"}
    # 各插件 matcher 均已注册且 owner 补齐
    assert all(m.owner in names for m in pm.all_matchers())


async def test_load_builtin_fallback_when_dir_missing(bot, monkeypatch):
    """打包环境：builtin 目录不可扫描时，回退到显式清单加载

    PyInstaller 打包后模块在 PYZ 归档内，文件系统扫描返回空；
    此处 mock pkgutil.iter_modules 返回空，验证回退 _BUILTIN_PLUGINS。
    """
    import pkgutil

    from bot.plugin.manager import _BUILTIN_PLUGINS

    monkeypatch.setattr(pkgutil, "iter_modules", lambda *a, **k: iter([]))
    pm = bot.plugin_manager
    await pm.load_builtin(bot)
    names = {p.name for p in pm.plugins.values()}
    assert names == set(_BUILTIN_PLUGINS)
