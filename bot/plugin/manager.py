# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""插件管理器（门面）

对外 API 与职责分组：
- 本模块（PluginManager）：插件注册表、调度缓存、加载/重载/卸载/启停、
  依赖解析、i18n、生命周期钩子分发、安装委托；
- manager_web.py（PluginWebMixin）：Web 管理页面挂载、插件级 Web API、
  配置 Schema / 配置值读写；
- manager_metrics.py（PluginMetricsMixin）：Matcher 执行指标记录与查询；
- _rwlock.py：热重载读写屏障；_loading.py：加载辅助纯函数。

支持两种 Matcher 注册方式：
1. 模块级装饰器 @on_command(...)：加载模块时自动收集
2. 插件内 self.matchers.append(...)：在 on_load 中手动注册
"""

import asyncio
import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from ._loading import (
    _BUILTIN_PLUGINS,
    _expand_subcommand_matchers,
    _load_plugin_json,
    _module_plugin_classes,
    _parse_version_spec,
    _sdk_plugin_base,
)
from ._rwlock import _ReloadRWLock
from .deps import ensure_dependencies, ensure_in_sys_path
from .installer import PluginInstaller
from .manager_metrics import PluginMetricsMixin
from .manager_web import PluginWebMixin
from .protocol.base import PluginBase, PluginStatus
from .protocol.matcher import (
    Matcher,
    begin_module_collection,
    end_module_collection,
)

logger = logging.getLogger("qingci-bot.plugin.manager")


class PluginManager(PluginWebMixin, PluginMetricsMixin):
    """插件管理器：加载、卸载、热重载、状态管理、指标监控"""

    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}
        # 安装/卸载职责独立成模块（来源拉取、归档安全解压、目录落位），
        # 管理器只保留插件生命周期与调度
        self._installer: PluginInstaller = PluginInstaller(self)
        self._cached_matchers: list[Matcher] | None = None
        # 热重载互斥锁：importlib.reload + 注册非原子，串行化避免并发
        # reload/install 竞态（新旧插件实例并存、matcher 双份）
        self._reload_lock = asyncio.Lock()
        # 重载读写屏障：事件分发共享读、重载独占写，防止重载窗口内
        # 分发读到半新半旧的模块 dict / 注册表
        self._reload_rw = _ReloadRWLock()
        # 调度缓存签名：记录构建缓存时各插件 matcher 数量，检测运行期动态增删
        self._matchers_sig: tuple | None = None
        # 事件类型倒排索引: event_type -> 该类型的 Matcher（保持优先级升序）
        self._matcher_index: dict[str, list[Matcher]] = {}
        # Matcher 执行指标: owner_name -> Matcher 实例 -> MatcherMetrics
        self._metrics: dict[str, dict[Matcher, Any]] = {}
        # plugin.json 预取缓存: module_path -> metadata dict
        self._metadata_cache: dict[str, dict] = {}
        # Web 管理页面注册信息: plugin_name -> [{"title": ..., "icon": ..., "static_dir": ...}]
        self._plugin_pages: dict[str, list[dict]] = {}
        # 插件级 Web API 注册信息: plugin_name -> [{"path": ..., "handler": ..., ...}]
        self._plugin_apis: dict[str, list[dict]] = {}
        # FastAPI 应用引用（由 create_app 后注入）
        self._web_app = None
        # 全局 i18n 语言（插件翻译默认语言，由 config.lang 设置）
        self._i18n_locale = "zh-CN"
        # 插件注册的 LLM 工具名: plugin_name -> [full_tool_name]，卸载时注销
        self._plugin_tools: dict[str, list[str]] = {}
        # 插件加载失败记录: plugin_name -> 错误摘要（供 WebUI 展示，替代静默失败）
        self._load_errors: dict[str, str] = {}

    # ---- i18n ----

    def set_i18n_locale(self, locale: str) -> None:
        """设置全局语言并刷新已加载插件的翻译"""
        self._i18n_locale = locale or "zh-CN"
        for plugin in self._plugins.values():
            self._load_i18n(plugin)

    def _load_i18n(self, plugin: PluginBase) -> None:
        """为插件加载翻译资源（i18n/<locale>.json）"""
        from ..i18n import load_plugin_i18n

        plugin.i18n.locale = self._i18n_locale
        plugin.i18n._data.clear()
        loaded = load_plugin_i18n(plugin)
        if loaded.locale == self._i18n_locale and loaded._data:
            plugin.i18n._data.update(loaded._data)

    # ---- 注册表与调度缓存 ----

    @property
    def plugins(self) -> dict[str, PluginBase]:
        return self._plugins

    def get(self, name: str) -> PluginBase | None:
        return self._plugins.get(name)

    def all_matchers(self, post_type: str | None = None) -> list[Matcher]:
        """收集所有已启用插件的 Matcher（用于调度），结果已按优先级升序排序

        约定：priority 越小越先执行。返回缓存副本，防止调用方污染缓存。
        仅 LOADED 状态的插件参与调度，disabled 的 Matcher 被跳过。

        post_type 非空时通过事件类型倒排索引直接返回该事件类型的 Matcher，
        避免每次分发都对全部 Matcher 做线性扫描过滤（注意/请求等低频
        事件类型收益尤其明显）。
        """
        if self._cached_matchers is None or self._matchers_sig != self._matchers_signature():
            self._matchers_sig = self._matchers_signature()
            result = []
            for plugin in self._plugins.values():
                if plugin.matchers and plugin.status == PluginStatus.LOADED:
                    for m in plugin.matchers:
                        if not m.disabled:
                            result.append(m)
            result.sort(key=lambda m: m.priority)
            self._cached_matchers = result
            # 构建事件类型倒排索引（各类型子列表随全量排序保持优先级升序）
            index: dict[str, list[Matcher]] = {}
            for m in result:
                index.setdefault(m.event_type, []).append(m)
            self._matcher_index = index
        if post_type:
            return list(self._matcher_index.get(post_type, []))
        return list(self._cached_matchers)

    def _matchers_signature(self) -> tuple:
        """当前已加载插件的 matcher 数量签名，用于检测运行期动态增删 matcher

        plugin.matchers 是 SDK 公开可变列表，插件可能在 on_load 之后运行时
        append/remove（一次性 matcher 等场景）；通过签名比对使调度缓存失效，
        避免新增 matcher 静默不参与调度。
        """
        return tuple(
            (name, len(p.matchers) if p.matchers else 0)
            for name, p in self._plugins.items()
            if p.status == PluginStatus.LOADED
        )

    def _invalidate_matchers_cache(self) -> None:
        self._cached_matchers = None
        self._matcher_index = {}

    # ---- 元数据发现 ----

    def discover_metadata(self, directory: Path) -> list[dict]:
        """扫描目录中的 plugin.json 元数据，无需导入模块

        目录型插件（plugins/<name>/plugin.json）为主形态；同时兼容
        plugins/ 根目录下直接放置的 plugin.json（文件型插件共用）。
        """
        results: list[dict] = []
        if not directory.is_dir():
            return results
        # 根目录直接放置的 plugin.json
        root_meta = _load_plugin_json(directory)
        if root_meta:
            self._metadata_cache["."] = root_meta
            results.append(root_meta)
        # 目录型插件：plugins/<name>/plugin.json
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            meta = _load_plugin_json(child)
            if meta:
                self._metadata_cache[child.name] = meta
                results.append(meta)
        return results

    # ---- 加载 ----

    async def load_builtin(self, bot) -> None:
        """加载内置插件

        优先通过 pkgutil 扫描 builtin 包目录（源码模式，可自动发现新插件）；
        PyInstaller 打包后该目录在文件系统中不存在（模块打入 PYZ 归档），
        pkgutil 扫描落空时回退到显式清单（与 qingci-bot-ce.spec 的 hiddenimports 保持一致）。
        """
        from . import builtin

        names: list[str] = []
        try:
            pkg_path = Path(builtin.__path__[0])
            for module_info in pkgutil.iter_modules([str(pkg_path)]):
                if module_info.name.startswith("_"):
                    continue
                names.append(module_info.name)
        except Exception:
            logger.debug("pkgutil 扫描内置插件失败，使用显式清单", exc_info=True)

        if not names:
            names = list(_BUILTIN_PLUGINS)

        for name in names:
            full_path = f"bot.plugin.builtin.{name}"
            try:
                await self._load_or_reload(full_path, bot)
            except Exception:
                logger.exception(f"加载内置插件失败: {name}")

    async def load_external(self, module_path: str, bot) -> bool:
        """加载外部插件"""
        name = module_path.rsplit(".", 1)[-1]
        try:
            await self._load_or_reload(module_path, bot)
            self._load_errors.pop(name, None)
            return True
        except Exception as e:
            self._load_errors[name] = f"{type(e).__name__}: {e}"
            logger.exception(f"加载外部插件失败: {module_path}")
            return False

    async def load_external_dir(self, bot, directory: Path | None = None) -> int:
        """扫描并加载外部插件目录

        支持两种插件形态：
        1. 目录型（推荐）：plugins/<name>/__init__.py，可含 web/、plugin.json
        2. 文件型（兼容）：plugins/<name>.py

        同名的目录型优先于文件型。

        Args:
            bot: Bot 实例
            directory: 插件目录路径，默认为 app_root()/plugins/

        Returns:
            成功加载的插件数量
        """
        if directory is None:
            from ..paths import plugins_dir

            directory = plugins_dir()

        root_str = str(directory.parent)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(f"无法创建插件目录: {directory}，跳过外部插件加载")
            return 0

        # 确保 plugins/__init__.py 存在（目录型和文件型都需要）
        init_file = directory / "__init__.py"
        if not init_file.exists():
            try:
                init_file.write_text(
                    "# Qingci-Bot CE 外部插件目录\n"
                    "# 将插件包（目录）或 .py 文件放入此目录即可自动加载\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.warning(f"无法写入 {init_file}，跳过外部插件加载")
                return 0

        # 收集需要加载的插件名（目录型优先）
        plugins_to_load: list[str] = []
        dir_names: set[str] = set()

        # 1. 扫描目录型插件（含 __init__.py 或 plugin.json 的目录）
        for subdir in sorted(directory.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith("_") or subdir.name.startswith("."):
                continue
            if (subdir / "__init__.py").is_file() or (subdir / "plugin.json").is_file():
                plugins_to_load.append(subdir.name)
                dir_names.add(subdir.name)

        # 2. 扫描文件型插件（未被目录型覆盖的 .py 文件）
        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = py_file.stem
            if module_name not in dir_names:
                plugins_to_load.append(module_name)

        # 加载
        count = 0
        auto_install = bool(getattr(bot.config.bot, "auto_install_plugin_deps", True))
        for module_name in plugins_to_load:
            module_path = f"plugins.{module_name}"
            # 目录型插件：加载前确保其第三方依赖已装到该插件独立的 deps 子目录并注入 sys.path
            plugin_dir = directory / module_name
            if plugin_dir.is_dir() and (plugin_dir / "__init__.py").is_file():
                try:
                    if auto_install:
                        await ensure_dependencies(plugin_dir)
                    else:
                        ensure_in_sys_path(plugin_dir.name)
                except Exception:
                    logger.exception(f"确保插件 {module_name} 依赖失败")
            try:
                ok = await self.load_external(module_path, bot)
                if ok:
                    count += 1
            except Exception as e:
                self._load_errors[module_name] = f"{type(e).__name__}: {e}"
                logger.exception(f"加载外部插件失败: {module_path}")

        if count > 0:
            logger.info(f"从外部插件目录加载了 {count} 个插件")
        return count

    async def _load_or_reload(
        self,
        full_path: str,
        bot,
        replaced_name: str | None = None,
        _loading: set | None = None,
    ) -> None:
        """加载或重载模块，确保模块级装饰器重新执行

        对已缓存的模块使用 reload，对新模块使用 import_module。
        始终包裹 begin/end collection 以收集模块级 Matcher 与 LLM 工具。

        Args:
            replaced_name: reload 场景下被替换插件的原注册名（插件可能改名，
                首次 load 传 None）
            _loading: 正在加载的模块名集合（依赖解析用，检测循环依赖）
        """
        from .llm_tool import begin_tool_collection, end_tool_collection

        collector = begin_module_collection()
        tool_collector = begin_tool_collection()
        sdk_base = _sdk_plugin_base()
        stale_classes: set | None = None
        try:
            if full_path in sys.modules:
                module = sys.modules[full_path]
                stale_classes = set(_module_plugin_classes(module, sdk_base))
                module = importlib.reload(module)
            else:
                module = importlib.import_module(full_path)
        finally:
            end_module_collection()
            end_tool_collection()

        await self._register_from_module(
            module, collector, bot, replaced_name, stale_classes, _loading, tool_collector
        )

    async def _ensure_dependencies(self, plugin: PluginBase, bot, loading: set) -> None:
        """确保插件声明的依赖已加载（支持 PEP 440 版本约束）

        - require 格式: "name" 或 "name>=1.0,<2.0"
        - 依赖已注册时校验版本
        - 未注册时尝试加载 bot.plugin.builtin.<name> 模块
        - 依赖缺失或循环依赖时抛出 ValueError
        """
        for dep in plugin.require or []:
            dep_name, version_spec = _parse_version_spec(dep)

            existing = self._plugins.get(dep_name)
            if existing is not None:
                if version_spec is not None:
                    try:
                        dep_version = Version(existing.version)
                    except InvalidVersion:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但 {dep_name} 版本号 {existing.version} 无效"
                        ) from None
                    if dep_version not in version_spec:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但当前版本为 {existing.version}"
                        )
                continue

            if dep_name in loading:
                chain = " -> ".join([*loading, dep_name])
                raise ValueError(f"插件循环依赖: {chain}")

            dep_module = f"bot.plugin.builtin.{dep_name}"
            try:
                importlib.import_module(dep_module)
            except ImportError:
                raise ValueError(
                    f"插件 {plugin.name} 依赖的插件 {dep_name} 不存在（找不到模块 {dep_module}）"
                ) from None
            await self._load_or_reload(dep_module, bot, _loading=loading)

            # 加载后再次校验版本
            if version_spec is not None:
                dep_plugin = self._plugins.get(dep_name)
                if dep_plugin is not None:
                    try:
                        dep_version = Version(dep_plugin.version)
                    except InvalidVersion:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但 {dep_name} 版本号 {dep_plugin.version} 无效"
                        ) from None
                    if dep_version not in version_spec:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但加载后版本为 {dep_plugin.version}"
                        )

    async def _register_from_module(
        self,
        module,
        collector: list[Matcher],
        bot,
        replaced_name: str | None = None,
        stale_classes: set | None = None,
        _loading: set | None = None,
        tool_collector: list | None = None,
    ) -> None:
        """从模块中查找 PluginBase 子类并注册"""
        from ..paths import data_root

        sdk_base = _sdk_plugin_base()
        plugin_classes = _module_plugin_classes(module, sdk_base)

        if stale_classes is not None:
            plugin_classes = [c for c in plugin_classes if c not in stale_classes]

        if not plugin_classes:
            if replaced_name is not None:
                raise ValueError(
                    f"模块 {module.__name__} 中未找到插件类，无法替换已注册的插件 {replaced_name}"
                )
            return

        if len(plugin_classes) > 1:
            raise ValueError(
                f"模块 {module.__name__} 定义了 {len(plugin_classes)} 个 PluginBase 子类，"
                f"每模块仅允许 1 个"
            )

        plugin_cls = plugin_classes[0]
        plugin = plugin_cls()

        # 独立 SDK 式插件：将 SDK 数据目录重定向到当前实例可写数据根，
        # 保证插件数据（DB/缓存/导出文件）遵循实例隔离，而非落在 SDK 默认目录。
        if sdk_base is not None and issubclass(plugin_cls, sdk_base):
            try:
                import qingci_plugin_sdk.paths as sdk_paths

                sdk_paths.set_data_root(data_root())
            except ImportError:
                logger.debug("qingci_plugin_sdk 未安装，跳过 SDK 数据目录重定向")

        # 依赖解析
        loading = _loading if _loading is not None else set()
        if plugin.name in loading:
            chain = " -> ".join([*loading, plugin.name])
            raise ValueError(f"插件循环依赖: {chain}")
        loading.add(plugin.name)
        try:
            await self._ensure_dependencies(plugin, bot, loading)

            target_name = replaced_name or plugin.name
            old_plugin = self._plugins.get(target_name)

            # 加载插件级配置
            await self._load_plugin_config(plugin, bot)

            # 初始化插件
            await self._init_plugin(plugin, bot)
            if plugin.matchers is None:
                plugin.matchers = []

            # 关联 Matcher
            for m in plugin.matchers:
                if not m.owner:
                    m.owner = plugin.name
            for m in collector:
                handler_mod = getattr(m.handler, "__module__", "") or ""
                if handler_mod and handler_mod != module.__name__:
                    continue
                m.owner = plugin.name
                plugin.matchers.append(m)

            # 展开 on_command 子指令 matcher（详见 _expand_subcommand_matchers）
            _expand_subcommand_matchers(plugin.matchers, plugin.name)

            # 成功：设置状态为 LOADED
            plugin._status = PluginStatus.LOADED

            old_tool_names = self._plugin_tools.get(target_name, [])
            # 注册表替换（卸载旧 + 挂新）全程持写锁：等待在途分发（读锁）
            # 结束，防止分发读到新旧混合的 Matcher / 页面 / 工具注册
            swap_guarded = await self._reload_rw.acquire_write()
            try:
                if old_plugin is not None:
                    await self.unload(target_name)
                existing = self._plugins.get(plugin.name)
                if existing is not None and existing is not old_plugin:
                    logger.warning(
                        f"插件重名覆盖: {plugin.name}（{type(existing).__module__} "
                        f"被 {module.__name__} 替换）"
                    )
                    # 重名覆盖：被覆盖的旧插件需正常卸载，清理其调度任务 / LLM
                    # 工具 / Web 页面注册，避免资源残留成为幽灵实例
                    await self.unload(plugin.name)
                self._plugins[plugin.name] = plugin
                # 注册插件声明的 LLM 工具（模块级 @llm_tool），卸载时注销
                if tool_collector:
                    from .llm_tool import register_tools

                    registered = register_tools(bot.tool_registry, plugin.name, tool_collector)
                    if registered:
                        self._plugin_tools[plugin.name] = registered
                        # 保留 collector，供重载失败时恢复旧插件工具注册
                        plugin._tool_collector = tool_collector
                # 重载场景下 unload 已清除页面注册，需重新收集
                # （若插件没有页面，_plugin_pages 保持为空即可）
                self._collect_plugin_pages(plugin)
                self._collect_plugin_apis(plugin)
            except BaseException:
                try:
                    await plugin.on_unload()
                except (Exception, asyncio.CancelledError):
                    logger.exception(f"插件 {plugin.name} 补偿 on_unload 异常")
                # 新插件注册失败：清理其已注册的资源，并尽力恢复旧插件，
                # 避免旧插件已被卸载、新插件未注册导致插件从管理器消失
                self._plugins.pop(plugin.name, None)
                self._plugin_tools.pop(plugin.name, None)
                self._remove_plugin_pages(plugin.name)
                self._remove_plugin_apis(plugin.name)
                if old_plugin is not None and target_name not in self._plugins:
                    self._plugins[target_name] = old_plugin
                    self._plugin_tools[target_name] = old_tool_names
                    old_collector = getattr(old_plugin, "_tool_collector", None)
                    if old_tool_names and old_collector and plugin.bot is not None:
                        from .llm_tool import register_tools

                        try:
                            register_tools(plugin.bot.tool_registry, target_name, old_collector)
                        except Exception:
                            logger.exception(f"恢复旧插件 {target_name} 工具注册失败")
                    self._invalidate_matchers_cache()
                    logger.warning(f"插件 {plugin.name} 注册失败，已恢复旧插件 {target_name}")
                raise
            finally:
                if swap_guarded:
                    await self._reload_rw.release_write()

            matcher_count = len(plugin.matchers) if plugin.matchers else 0
            logger.info(
                f"插件已加载: {plugin.name} v{plugin.version}"
                f" (matchers: {matcher_count}, category: {plugin.category or '未分类'})"
            )
            self._invalidate_matchers_cache()
        except Exception:
            plugin._status = PluginStatus.ERROR
            raise
        finally:
            loading.discard(plugin.name)

    # ---- 插件级配置 ----

    async def _load_plugin_config(self, plugin: PluginBase, bot) -> None:
        """从 config.yaml 加载插件级配置

        查找路径：plugins.<plugin_name> 节
        若插件定义了 Config 内嵌类，使用 pydantic 校验；
        否则将原始 dict 赋值给 plugin_config。
        """
        config_dict = bot.config.get_plugin_config(plugin.name)
        if config_dict is None:
            return

        # 检查插件是否定义了 Config 内嵌类
        config_cls = getattr(type(plugin), "Config", None)
        if config_cls is not None and isinstance(config_cls, type):
            try:
                plugin.plugin_config = config_cls(**config_dict)
            except Exception as e:
                logger.warning(f"插件 {plugin.name} 配置校验失败: {e}，使用原始 dict")
                plugin.plugin_config = config_dict
        else:
            plugin.plugin_config = config_dict

        # M18：配置更新后通知插件刷新运行期快照（插件可定义 on_config_update
        # 把新配置热应用到自身状态，避免必须重载插件才生效）。
        try:
            hook = getattr(plugin, "on_config_update", None)
            if callable(hook):
                await hook()
        except Exception:
            logger.exception(f"插件 {plugin.name} on_config_update 异常")

    # ---- 卸载 ----

    async def unload(self, name: str) -> None:
        """卸载插件"""
        plugin = self._plugins.pop(name, None)
        if plugin:
            plugin._status = PluginStatus.UNLOADING
            try:
                await plugin.on_unload()
            except Exception:
                logger.exception(f"插件 {name} on_unload 异常")
            scheduler = getattr(plugin.bot, "scheduler", None) if plugin.bot else None
            if scheduler is not None:
                try:
                    scheduler.remove_jobs_by_owner(name)
                except Exception:
                    logger.exception(f"清理插件 {name} 定时任务异常")
            # 清理指标
            self._metrics.pop(name, None)
            # 注销插件注册的 LLM 工具
            tool_names = self._plugin_tools.pop(name, [])
            # SDK 将注入的 bot 标为 object | None，用 getattr 取注册表（兼作存在性判断）
            registry = (
                getattr(plugin.bot, "tool_registry", None) if plugin.bot is not None else None
            )
            if tool_names and registry is not None:
                for full_name in tool_names:
                    registry.unregister(full_name)
            # 清理 Web 管理页面注册
            self._remove_plugin_pages(name)
            # 清理插件级 Web API 注册（已挂载路由保留，请求时动态解析失效）
            self._remove_plugin_apis(name)
            # 清理该插件名下挂起的会话阶梯（多轮交互等待态）
            dispatcher = getattr(plugin.bot, "dispatcher", None) if plugin.bot else None
            if dispatcher is not None:
                try:
                    await dispatcher.clear_steps_for(name)
                except Exception:
                    logger.exception(f"清理插件 {name} 会话阶梯异常")
            logger.info(f"插件已卸载: {name}")
        # 清理加载错误记录（已卸载则不再展示 error 状态幽灵条目）
        self._load_errors.pop(name, None)
        self._invalidate_matchers_cache()

    # ---- 禁用/启用 ----

    async def disable(self, name: str) -> None:
        """禁用插件：跳过事件分发，保留实例（不触发 on_unload）"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"禁用失败：插件 {name} 不存在")
            return
        if plugin.status == PluginStatus.DISABLED:
            logger.info(f"插件 {name} 已处于禁用状态，跳过")
            return
        if plugin.status != PluginStatus.LOADED:
            logger.warning(f"插件 {name} 状态为 {plugin.status.value}，无法禁用")
            return
        plugin._status = PluginStatus.DISABLED
        try:
            await plugin.on_disable()
        except Exception:
            logger.exception(f"插件 {name} on_disable 异常")
        # 禁用时清除该插件名下挂起的会话阶梯（多轮交互等待态不再续接）
        dispatcher = getattr(plugin.bot, "dispatcher", None) if plugin.bot else None
        if dispatcher is not None:
            try:
                await dispatcher.clear_steps_for(name)
            except Exception:
                logger.exception(f"清理插件 {name} 会话阶梯异常")
        self._invalidate_matchers_cache()
        logger.info(f"插件已禁用: {name}")

    async def enable(self, name: str) -> None:
        """启用插件：恢复事件分发（不触发 on_load）"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"启用失败：插件 {name} 不存在")
            return
        if plugin.status == PluginStatus.LOADED:
            logger.info(f"插件 {name} 已处于启用状态，跳过")
            return
        if plugin.status != PluginStatus.DISABLED:
            logger.warning(f"插件 {name} 状态为 {plugin.status.value}，无法启用")
            return
        plugin._status = PluginStatus.LOADED
        try:
            await plugin.on_enable()
        except Exception:
            logger.exception(f"插件 {name} on_enable 异常")
        self._invalidate_matchers_cache()
        logger.info(f"插件已启用: {name}")

    def is_enabled(self, name: str) -> bool:
        """查询插件是否启用（不存在返回 False）"""
        plugin = self._plugins.get(name)
        return plugin.enabled if plugin else False

    # ---- 重载 ----

    async def reload(self, name: str, bot) -> None:
        """重载插件（_reload_lock 串行化，避免并发 reload 竞态）

        重载全程持有读写屏障写锁：等待在途事件分发（读锁）结束后
        才执行模块 reload + 注册表替换，防止分发读到半新半旧状态。
        """
        async with self._reload_lock:
            plugin = self._plugins.get(name)
            if not plugin:
                logger.warning(f"重载失败：插件 {name} 不存在")
                return

            module_path = type(plugin).__module__
            # 同任务重入（handler 内触发 reload）时 acquire_write 返回 False，
            # 此时跳过屏障（读锁仍由本任务持有，注册表替换与分发为同一任务串行）
            guarded = await self._reload_rw.acquire_write()
            try:
                await self._load_or_reload(module_path, bot, replaced_name=name)
            except Exception as e:
                # 记录加载错误供 WebUI 展示（reload 失败时实例状态与磁盘/模块脱节）
                self._load_errors[name] = f"{type(e).__name__}: {e}"
                logger.exception(f"重载插件 {name} 失败，旧插件保持生效")
                raise
            finally:
                if guarded:
                    await self._reload_rw.release_write()
                self._invalidate_matchers_cache()

    # ---- 初始化 ----

    async def _init_plugin(self, plugin: PluginBase, bot) -> None:
        """初始化插件依赖并调用 on_load

        优先使用 DI 容器自动注入（按类型匹配），
        兼容手动赋值（确保向后兼容）。
        """
        # 先用 DI 容器自动注入（按类型注解匹配）
        if hasattr(bot, "di") and bot.di is not None:
            await bot.di.inject(plugin, skip_missing=True)

        # 手动赋值兜底（确保 DI 未覆盖的字段也有值）
        plugin.bot = plugin.bot or bot
        plugin.db = plugin.db or bot.db
        plugin.config = plugin.config or bot.config
        plugin.connection = plugin.connection or bot.connection
        plugin.llm = plugin.llm or bot.llm
        plugin.scheduler = plugin.scheduler or bot.scheduler
        plugin.tool_registry = plugin.tool_registry or bot.tool_registry
        plugin.knowledge_store = plugin.knowledge_store or bot.knowledge_store
        plugin.session_state = plugin.session_state or bot.session_state
        plugin.event_bus = plugin.event_bus or getattr(bot, "event_bus", None)
        plugin.matchers = plugin.matchers or []
        plugin._status = PluginStatus.LOADING
        await plugin.on_load()
        # 收集插件注册的 Web 管理页面
        self._collect_plugin_pages(plugin)
        # 收集插件注册的 Web API（挂载到 /api/plugin-web/<name>/）
        self._collect_plugin_apis(plugin)
        # 加载插件 i18n 翻译资源
        self._load_i18n(plugin)

    # ---- 关闭 ----

    async def shutdown(self) -> None:
        """关闭所有插件"""
        for name in list(self._plugins.keys()):
            await self.unload(name)

    # ---- 全局生命周期钩子 ----

    async def dispatch_lifecycle(self, hook: str, *args, **kwargs) -> None:
        """向所有已加载插件分发全局生命周期钩子（异常隔离）

        仅调用实际覆写了该钩子的插件（跳过基类空实现），
        避免空转。支持 on_startup / on_shutdown / on_bot_connect / on_metaevent。
        """
        base = getattr(PluginBase, hook, None)
        for plugin in list(self._plugins.values()):
            if plugin.status != PluginStatus.LOADED:
                continue
            fn = getattr(type(plugin), hook, None)
            if fn is None or fn is base:
                continue
            try:
                res = getattr(plugin, hook)(*args, **kwargs)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                logger.exception(f"插件 {plugin.name} {hook} 钩子异常")

    # ---- 在线安装（实现在 installer.py）----

    async def install(
        self,
        bot: Any,
        source: str,
        *,
        name: str | None = None,
        allow_local: bool = False,
        expected_sha256: str = "",
    ) -> bool:
        """在线安装插件到外部插件目录并加载（委托 PluginInstaller）

        source 支持 git 仓库、HTTP 归档 URL、本地路径（仅 allow_local=True）。
        安全校验（SSRF / 归档炸弹 / Zip Slip / 插件名穿越）全部在
        installer.py 内完成，本方法只做职责转发。
        """
        return await self._installer.install(
            bot,
            source,
            name=name,
            allow_local=allow_local,
            expected_sha256=expected_sha256,
        )

    async def remove(self, name: str, *, purge: bool = False) -> None:
        """卸载插件；purge=True 时连同数据与依赖彻底删除（委托 PluginInstaller）"""
        await self._installer.remove(name, purge=purge)

    # ---- 工具 ----

    def remove_temp_matcher(self, matcher: Matcher) -> None:
        """移除一次性（temp）匹配器并失效缓存"""
        if not getattr(matcher, "owner", ""):
            return
        plugin = self._plugins.get(matcher.owner)
        if plugin is not None and plugin.matchers and matcher in plugin.matchers:
            plugin.matchers.remove(matcher)
            self._invalidate_matchers_cache()
