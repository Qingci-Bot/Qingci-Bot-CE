"""PluginManager 插件管理器测试：加载/重载/依赖/循环依赖/多类拒绝"""

import asyncio
import json

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
    # 加载失败应记录错误（供 WebUI 展示，避免静默失败）
    assert "no_such_module" in bot.plugin_manager._load_errors


async def test_load_failure_error_cleared_on_success(bot):
    pm = bot.plugin_manager
    await pm.load_external("plugin_pkg.no_such_module", bot)
    assert "no_such_module" in pm._load_errors
    ok = await pm.load_external("plugin_pkg.simple_plugin", bot)
    assert ok is True
    assert "simple" not in pm._load_errors


async def test_load_sdk_plugin(bot):
    """基于独立插件 SDK（qingci_plugin_sdk）的插件应被识别并加载"""
    from bot.paths import data_root

    ok = await bot.plugin_manager.load_external("plugin_pkg.sdk_plugin", bot)
    assert ok is True

    plugin = bot.plugin_manager.get("sdk_plugin")
    assert plugin is not None
    assert plugin.name == "sdk_plugin"
    assert len(plugin.matchers) == 1
    assert plugin.matchers[0].owner == "sdk_plugin"

    # SDK 插件数据目录应重定向到 bot 的可写数据根（实例隔离），
    # 而非 SDK 包默认的 Plugins-SDK/data/plugins/<name>。
    sdk_plugin_dir = plugin.data_dir
    assert sdk_plugin_dir == data_root() / "plugins" / "sdk_plugin"
    assert sdk_plugin_dir.is_dir()


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


async def test_remove_deletes_disk_dir(bot, tmp_path):
    """remove 应卸载插件并删除磁盘插件目录（修复卸载后文件残留）"""
    from bot import paths

    old_root = paths.plugins_dir()
    paths.set_plugins_dir(tmp_path)
    try:
        plugin_dir = tmp_path / "simple"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            '{"name": "simple", "version": "1.0.0"}', encoding="utf-8"
        )

        pm = bot.plugin_manager
        await pm.load_external("plugin_pkg.simple_plugin", bot)
        assert pm.get("simple") is not None
        assert plugin_dir.is_dir()

        await pm.remove("simple")
        assert pm.get("simple") is None
        assert pm.all_matchers() == []
        assert not plugin_dir.exists()
    finally:
        paths.set_plugins_dir(old_root)


async def test_remove_without_disk_dir_only_unloads(bot):
    """无磁盘插件目录时 remove 退化为仅卸载，不报错"""
    pm = bot.plugin_manager
    await pm.load_external("plugin_pkg.simple_plugin", bot)
    assert pm.get("simple") is not None

    await pm.remove("simple")
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


async def test_matchers_dynamic_append_picked_up_without_invalidation(bot):
    """运行期动态 append matcher（不手动失效缓存）也应被调度识别（签名检测）"""
    pm = bot.plugin_manager
    await pm.load_external("plugin_pkg.simple_plugin", bot)
    plugin = pm.get("simple")
    before = len(pm.all_matchers())
    plugin.matchers.append(_make_matcher("dyn", 99))
    after = pm.all_matchers()
    assert len(after) == before + 1
    assert "dyn" in [m.owner for m in after]


async def test_copy_plugin_dir_rejects_invalid_name(bot, tmp_path):
    """非法插件名（路径穿越）必须拒绝安装"""
    pm = bot.plugin_manager
    src = tmp_path / "evil"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    name, target = await pm._copy_plugin_dir(src, "..", plugins_dir)
    assert name == ""
    assert target is None
    # 未发生路径穿越：plugins 目录未写入任何内容
    assert list(plugins_dir.iterdir()) == []


async def test_copy_plugin_dir_keeps_old_on_failure(bot, tmp_path):
    """复制失败时保留旧版本插件目录（staging 原子替换）"""
    pm = bot.plugin_manager
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    target = plugins_dir / "demo"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")

    # 源目录不存在 → copytree 抛 OSError，旧目录必须保留
    name, result = await pm._copy_plugin_dir(tmp_path / "missing-src", "demo", plugins_dir)
    assert result is None
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"


async def test_discover_metadata_scans_dir_plugins(bot, tmp_path):
    """discover_metadata 应扫描目录型插件 plugins/<name>/plugin.json"""
    pm = bot.plugin_manager
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "alpha").mkdir(parents=True)
    (plugins_dir / "alpha" / "plugin.json").write_text(
        json.dumps({"name": "alpha", "version": "1.0.0"}), encoding="utf-8"
    )
    metas = pm.discover_metadata(plugins_dir)
    assert len(metas) == 1
    assert metas[0]["name"] == "alpha"
    assert pm._metadata_cache["alpha"]["version"] == "1.0.0"


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


# ──────────────────────────────────────────────────────────────────
# 插件热重载 watcher：加载失败的插件修复文件后应触发重试加载
# ──────────────────────────────────────────────────────────────────


class _WatcherFakeManager:
    """最小 manager 替身：只记录 load_external 调用与 _load_errors 状态"""

    def __init__(self, load_errors: dict[str, str], loaded: set[str] | None = None):
        self._load_errors = dict(load_errors)
        self.loaded = set(loaded or ())
        self.calls: list[str] = []

    def get(self, name):
        return object() if name in self.loaded else None

    async def load_external(self, module_path, bot):
        self.calls.append(module_path)
        self._load_errors.pop(module_path.rsplit(".", 1)[-1], None)
        return True


async def test_watcher_retries_failed_plugin_on_file_change():
    """加载失败的插件（在 _load_errors 中）文件变更时，watcher 应触发重试加载"""
    from pathlib import Path

    from bot.plugin.watcher import PluginWatcher

    mgr = _WatcherFakeManager(load_errors={"broken": "ImportError: boom"})
    watcher = PluginWatcher(mgr, object(), Path("/tmp/plugins"))
    await watcher._reload_plugin("/tmp/plugins/broken/__init__.py")

    assert mgr.calls == ["plugins.broken"]
    # 重试加载成功后错误记录被清除
    assert "broken" not in mgr._load_errors


async def test_watcher_does_not_retry_unknown_plugin():
    """插件未加载且不在 _load_errors 中（如新文件首次扫描）时，不触发加载"""
    from pathlib import Path

    from bot.plugin.watcher import PluginWatcher

    mgr = _WatcherFakeManager(load_errors={})
    watcher = PluginWatcher(mgr, object(), Path("/tmp/plugins"))
    await watcher._reload_plugin("/tmp/plugins/other/__init__.py")

    assert mgr.calls == []


# ──────────────────────────────────────────────────────────────────
# 重载读写屏障（_ReloadRWLock）：分发共享读、重载独占写、同任务重入安全
# ──────────────────────────────────────────────────────────────────


async def test_reload_rwlock_read_shared_write_exclusive():
    """读者可并发持有；写者在读者全部释放前阻塞"""
    from bot.plugin.manager import _ReloadRWLock

    rw = _ReloadRWLock()
    entered: list[str] = []

    async def hold_read(seconds: float):
        await rw.acquire_read()
        entered.append("read")
        try:
            await asyncio.sleep(seconds)
        finally:
            await rw.release_read()

    async def try_write():
        await rw.acquire_write()
        entered.append("write")
        await rw.release_write()

    # 两个读者并发持有（共享读不互斥）
    r1 = asyncio.create_task(hold_read(0.2))
    await asyncio.sleep(0.01)
    r2 = asyncio.create_task(hold_read(0.2))
    await asyncio.sleep(0.01)
    assert entered.count("read") == 2

    # 写者阻塞：读者全部释放前不得进入
    writer = asyncio.create_task(try_write())
    await asyncio.sleep(0.01)
    assert "write" not in entered

    await asyncio.sleep(0.25)  # 读者超时释放
    await asyncio.gather(r1, r2, writer)
    assert "write" in entered

    # 写者释放后读者可进入
    await rw.acquire_read()
    await rw.release_read()


async def test_reload_rwlock_reentrancy():
    """同任务读→写跳过（返回 False）；写锁内重入读/写均放行"""
    from bot.plugin.manager import _ReloadRWLock

    rw = _ReloadRWLock()
    await rw.acquire_read()
    # 读→写重入：返回 False 表示调用方应跳过写锁
    assert await rw.acquire_write() is False
    await rw.release_read()

    # 写锁内重入
    assert await rw.acquire_write() is True
    await rw.acquire_read()  # 写→读重入放行
    await rw.release_read()
    assert await rw.acquire_write() is True  # 嵌套写幂等放行
    await rw.release_write()
    await rw.release_write()  # 幂等释放
    # 写锁已释放：新读者可进入
    await rw.acquire_read()
    await rw.release_read()


# ──────────────────────────────────────────────────────────────────
# 市场安装归档完整性校验（source_sha256）
# ──────────────────────────────────────────────────────────────────


async def test_install_archive_sha256_mismatch_rejected(bot, tmp_path, monkeypatch):
    """HTTP 归档 sha256 不匹配时拒绝安装（防传输篡改/投毒）"""
    import shutil
    import zipfile

    from bot.plugin.manager import _sha256_file

    manager = bot.plugin_manager
    # 构造真实 zip 归档
    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("shaplug/__init__.py", "NAME = 'shaplug'\n")
    real_sha = _sha256_file(archive)

    # 拦截 _download_archive：把归档内容写入目标路径（staging）
    async def fake_download(url, dest):
        shutil.copyfile(archive, dest)
        return True

    monkeypatch.setattr(manager, "_download_archive", fake_download)

    target_dir = tmp_path / "installed"

    # 错误的 sha256 → 拒绝安装
    _, result = await manager._fetch_plugin(
        "https://example.com/plugin.zip",
        "shaplug",
        target_dir,
        expected_sha256="0" * 64,
    )
    assert result is None

    # 正确的 sha256 → 安装成功
    name, result = await manager._fetch_plugin(
        "https://example.com/plugin.zip",
        "shaplug",
        target_dir,
        expected_sha256=real_sha,
    )
    assert result is not None
    assert name == "shaplug"
    assert (result / "__init__.py").exists()


async def test_watcher_reloads_loaded_plugin():
    """已加载插件文件变更仍走 reload 路径（不经过 load_external）"""
    from pathlib import Path

    from bot.plugin.watcher import PluginWatcher

    mgr = _WatcherFakeManager(load_errors={}, loaded={"simple"})
    watcher = PluginWatcher(mgr, object(), Path("/tmp/plugins"))
    # reload 走 _manager.reload（未在 FakeManager 定义即 AttributeError）
    # → 被 watcher 内部 except 捕获，load_external 不应被调用
    await watcher._reload_plugin("/tmp/plugins/simple/__init__.py")

    assert mgr.calls == []
