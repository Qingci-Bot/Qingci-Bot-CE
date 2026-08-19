"""插件管理器"""

import asyncio
import importlib
import json
import logging
import os
import pkgutil
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from ._proc import NO_WINDOW_FLAG
from .base import PluginBase, PluginStatus
from .deps import ensure_dependencies, ensure_in_sys_path
from .matcher import Matcher, begin_module_collection, end_module_collection

logger = logging.getLogger("qingci-bot.plugin.manager")

# 内置插件显式清单（PyInstaller 打包后 pkgutil 无法扫描 PYZ 归档内模块，
# 需回退到显式清单加载；新增内置插件时同步更新此处与 qingci-bot-ce.spec 的 hiddenimports）
_BUILTIN_PLUGINS: tuple[str, ...] = ("admin", "chat", "help", "imagegen", "knowledge")


@dataclass
class MatcherMetrics:
    """单个 Matcher 的执行指标"""

    call_count: int = 0
    total_time_ms: float = 0.0
    error_count: int = 0
    last_call_time: float = 0.0

    @property
    def avg_time_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_time_ms / self.call_count


def _parse_version_spec(dep_spec: str) -> tuple[str, SpecifierSet | None]:
    """解析依赖声明，返回 (name, version_spec)。

    示例：
        "chat"           -> ("chat", None)
        "chat>=1.0"      -> ("chat", SpecifierSet(">=1.0"))
        "chat>=1.0,<2.0" -> ("chat", SpecifierSet(">=1.0,<2.0"))
    """
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9_]*)\s*(.*)$", dep_spec)
    if not match:
        raise ValueError(f"无效的依赖声明: {dep_spec}")
    name = match.group(1)
    spec_str = match.group(2).strip()
    if spec_str:
        try:
            return name, SpecifierSet(spec_str)
        except Exception:
            raise ValueError(f"无效的版本约束: {dep_spec}") from None
    return name, None


def _load_plugin_json(directory: Path) -> dict | None:
    """从目录中加载 plugin.json 元数据（若存在）"""
    json_path = directory / "plugin.json"
    if not json_path.is_file():
        return None
    try:
        return cast(dict, json.loads(json_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        logger.warning(f"plugin.json 解析失败: {json_path}")
        return None


def _sdk_plugin_base():
    """惰性获取独立插件 SDK 的 PluginBase 基类（SDK 未安装时返回 None）

    外部插件可能基于 bot.plugin.base.PluginBase（内置式）或
    qingci_plugin_sdk.base.PluginBase（独立 SDK 式）开发，两者是镜像类、
    无继承关系。管理器需同时识别这两类插件。
    """
    try:
        from qingci_plugin_sdk.base import PluginBase as _SdkBase

        return _SdkBase
    except ImportError:
        return None


def _module_plugin_classes(module, sdk_base) -> list[type]:
    """返回模块中定义的插件类（bot 基类或 SDK 基类的直接子类）"""
    result: list[type] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if not isinstance(attr, type) or attr.__module__ != module.__name__:
            continue
        if attr is PluginBase or (sdk_base is not None and attr is sdk_base):
            continue
        if issubclass(attr, PluginBase):
            result.append(attr)
        elif sdk_base is not None and issubclass(attr, sdk_base):
            result.append(attr)
    return result


class PluginManager:
    """插件管理器：加载、卸载、热重载、状态管理、指标监控

    支持两种 Matcher 注册方式：
    1. 模块级装饰器 @on_command(...)：加载模块时自动收集
    2. 插件内 self.matchers.append(...)：在 on_load 中手动注册
    """

    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}
        self._cached_matchers: list[Matcher] | None = None
        # 事件类型倒排索引: event_type -> 该类型的 Matcher（保持优先级升序）
        self._matcher_index: dict[str, list[Matcher]] = {}
        # Matcher 执行指标: owner_name -> Matcher 实例 -> MatcherMetrics
        self._metrics: dict[str, dict[Matcher, MatcherMetrics]] = {}
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
        if self._cached_matchers is None:
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

    def _invalidate_matchers_cache(self) -> None:
        self._cached_matchers = None
        self._matcher_index = {}

    # ---- 指标 ----

    def record_metric(self, matcher: Matcher, elapsed_ms: float, is_error: bool = False) -> None:
        """记录一次 Matcher 执行指标"""
        owner = getattr(matcher, "owner", "__unknown__")
        if owner not in self._metrics:
            self._metrics[owner] = {}
        if matcher not in self._metrics[owner]:
            self._metrics[owner][matcher] = MatcherMetrics()
        m = self._metrics[owner][matcher]
        m.call_count += 1
        m.total_time_ms += elapsed_ms
        if is_error:
            m.error_count += 1
        m.last_call_time = time.time()

    def get_metrics(self, plugin_name: str) -> list[dict]:
        """获取指定插件的执行指标"""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return []
        result = []
        for matcher in plugin.matchers or []:
            owner_metrics = self._metrics.get(plugin_name, {})
            m = owner_metrics.get(matcher)
            if m is None:
                continue
            handler_name = getattr(matcher.handler, "__name__", "unknown")
            result.append(
                {
                    "handler": handler_name,
                    "event_type": matcher.event_type,
                    "priority": matcher.priority,
                    "description": (matcher.meta or {}).get("description", ""),
                    "call_count": m.call_count,
                    "avg_time_ms": round(m.avg_time_ms, 2),
                    "total_time_ms": round(m.total_time_ms, 2),
                    "error_count": m.error_count,
                    "last_call_time": m.last_call_time,
                }
            )
        return result

    # ---- Web 管理页面 ----

    def set_web_app(self, app) -> None:
        """注入 FastAPI 应用引用（由 create_app 后调用），并挂载已注册的插件页面与 API"""
        self._web_app = app
        self._mount_all_plugin_pages()
        self._mount_all_plugin_apis()

    def _mount_all_plugin_pages(self) -> None:
        """挂载所有已注册插件的静态文件目录"""
        if self._web_app is None:
            return
        from fastapi.staticfiles import StaticFiles

        for plugin_name, pages in self._plugin_pages.items():
            for page in pages:
                static_dir = page.get("static_dir", "")
                if static_dir and os.path.isdir(static_dir):
                    mount_path = f"/api/plugin-data/{plugin_name}"
                    # 避免重复挂载
                    if not any(
                        r.path == mount_path for r in self._web_app.routes if hasattr(r, "path")
                    ):
                        try:
                            self._web_app.mount(
                                mount_path,
                                StaticFiles(directory=static_dir, html=True),
                                name=f"plugin-static-{plugin_name}",
                            )
                            logger.info(
                                f"插件 {plugin_name} 管理页面已挂载: {mount_path} -> {static_dir}"
                            )
                        except Exception:
                            logger.exception(f"挂载插件 {plugin_name} 管理页面失败: {static_dir}")

    def get_plugin_pages(self, name: str) -> list[dict]:
        """获取指定插件的管理页面注册信息（不含 static_dir）"""
        pages = self._plugin_pages.get(name, [])
        return [{"title": p["title"], "icon": p["icon"]} for p in pages]

    # ---- 配置 schema 自动生成配置 UI ----

    def get_config_schema(self, name: str) -> dict | None:
        """获取插件的配置 JSON Schema（用于自动渲染 Web 配置表单）

        插件定义 Config 内嵌类（pydantic BaseModel）时，返回其 JSON Schema；
        定义为普通类时，从 __annotations__ 兜底生成简单 schema；
        未定义 Config 时返回 None。
        """
        plugin = self._plugins.get(name)
        if not plugin:
            return None
        config_cls = getattr(type(plugin), "Config", None)
        if config_cls is None or not isinstance(config_cls, type):
            return None
        # pydantic v2：直接导出 JSON Schema（含默认值、必填字段、描述）
        if hasattr(config_cls, "model_json_schema"):
            try:
                return cast(dict, config_cls.model_json_schema())
            except Exception:
                pass
        return _schema_from_annotations(config_cls)

    def get_config_values(self, name: str) -> dict:
        """获取插件当前配置值（dict 形式）"""
        plugin = self._plugins.get(name)
        if not plugin or plugin.plugin_config is None:
            return {}
        cfg = plugin.plugin_config
        if hasattr(cfg, "model_dump"):
            try:
                return cast(dict, cfg.model_dump())
            except Exception:
                pass
        return dict(cfg)

    async def update_config(self, name: str, values: dict, bot) -> bool:
        """更新插件配置：写入 config.yaml 并重新校验应用到插件实例

        Args:
            name: 插件名
            values: 新配置值（dict）
            bot: Bot 实例

        Returns:
            是否成功
        """
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        try:
            bot.config.set_plugin_config(name, values)
            await self._load_plugin_config(plugin, bot)
            return True
        except Exception:
            logger.exception(f"更新插件 {name} 配置失败")
            return False

    def _collect_plugin_pages(self, plugin: PluginBase) -> None:
        """从插件实例收集已注册的 Web 管理页面"""
        if plugin._pages:
            self._plugin_pages[plugin.name] = list(plugin._pages)
            if self._web_app is not None:
                self._mount_all_plugin_pages()

    def _remove_plugin_pages(self, name: str) -> None:
        """移除插件的 Web 管理页面注册"""
        self._plugin_pages.pop(name, None)

    # ---- 插件级 Web API ----

    def _mount_all_plugin_apis(self) -> None:
        """挂载所有已注册插件的 Web API 路由"""
        if self._web_app is None:
            return
        from .webapi import mount_plugin_apis

        for plugin_name, apis in self._plugin_apis.items():
            try:
                mount_plugin_apis(self._web_app, self, plugin_name, apis)
            except Exception:
                logger.exception(f"挂载插件 {plugin_name} Web API 失败")

    def _collect_plugin_apis(self, plugin: PluginBase) -> None:
        """从插件实例收集已注册的 Web API 并挂载"""
        apis = getattr(plugin, "_apis", None)
        if apis:
            self._plugin_apis[plugin.name] = list(apis)
            if self._web_app is not None:
                self._mount_all_plugin_apis()

    def _remove_plugin_apis(self, name: str) -> None:
        """移除插件的 Web API 注册（已挂载路由保留，请求时动态解析失效）"""
        self._plugin_apis.pop(name, None)

    # ---- 元数据发现 ----

    def discover_metadata(self, directory: Path) -> list[dict]:
        """扫描目录中的 plugin.json 元数据，无需导入模块"""
        results: list[dict] = []
        if not directory.is_dir():
            return results
        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            # 尝试同目录下的 plugin.json
            meta = _load_plugin_json(py_file.parent)
            if meta:
                self._metadata_cache[py_file.stem] = meta
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
        try:
            await self._load_or_reload(module_path, bot)
            return True
        except Exception:
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
            # 目录型插件：加载前确保其第三方依赖已装到实例 deps 目录并注入 sys.path
            plugin_dir = directory / module_name
            if plugin_dir.is_dir() and (plugin_dir / "__init__.py").is_file():
                try:
                    if auto_install:
                        await ensure_dependencies(plugin_dir)
                    else:
                        ensure_in_sys_path()
                except Exception:
                    logger.exception(f"确保插件 {module_name} 依赖失败")
            try:
                ok = await self.load_external(module_path, bot)
                if ok:
                    count += 1
            except Exception:
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

            # 成功：设置状态为 LOADED
            plugin._status = PluginStatus.LOADED

            try:
                if old_plugin is not None:
                    await self.unload(target_name)
                existing = self._plugins.get(plugin.name)
                if existing is not None and existing is not old_plugin:
                    logger.warning(
                        f"插件重名覆盖: {plugin.name}（{type(existing).__module__} "
                        f"被 {module.__name__} 替换）"
                    )
                self._plugins[plugin.name] = plugin
                # 注册插件声明的 LLM 工具（模块级 @llm_tool），卸载时注销
                if tool_collector:
                    from .llm_tool import register_tools

                    registered = register_tools(bot.tool_registry, plugin.name, tool_collector)
                    if registered:
                        self._plugin_tools[plugin.name] = registered
                # 重载场景下 unload 已清除页面注册，需重新收集
                # （若插件没有页面，_plugin_pages 保持为空即可）
                self._collect_plugin_pages(plugin)
                self._collect_plugin_apis(plugin)
            except BaseException:
                try:
                    await plugin.on_unload()
                except (Exception, asyncio.CancelledError):
                    logger.exception(f"插件 {plugin.name} 补偿 on_unload 异常")
                raise

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
            if tool_names and plugin.bot is not None and getattr(plugin.bot, "tool_registry", None):
                for full_name in tool_names:
                    plugin.bot.tool_registry.unregister(full_name)
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
        """重载插件"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"重载失败：插件 {name} 不存在")
            return

        module_path = type(plugin).__module__
        try:
            await self._load_or_reload(module_path, bot, replaced_name=name)
        except Exception:
            logger.exception(f"重载插件 {name} 失败，旧插件保持生效")
            raise
        finally:
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

    # ---- 在线安装 ----

    async def install(self, bot, source: str, *, name: str | None = None) -> bool:
        """在线安装插件到外部插件目录并加载

        source 支持：
        - git 仓库地址（git+https://... / https://... / git@...）
        - HTTP 指向 zip/tar 归档的 URL
        - 本地路径（目录或归档文件）

        安装过程：拉取到 plugins/<name>/ → 加载 requirements.txt 依赖 → 加载插件。

        Args:
            bot: Bot 实例（用于加载）
            source: 插件来源
            name: 目标插件名（为空时从来源推断）

        Returns:
            是否成功安装并加载
        """
        from ..paths import plugins_dir

        plugin_dir = plugins_dir()
        plugin_dir.mkdir(parents=True, exist_ok=True)

        target_name, target_dir = await self._fetch_plugin(source, name, plugin_dir)
        if target_dir is None:
            return False

        # 自动安装依赖（requirements.txt / plugin.json 的 requirements 字段，
        # 装入实例隔离的 deps 目录而非全局环境）
        await ensure_dependencies(target_dir)

        # 加载插件
        module_path = f"plugins.{target_name}"
        try:
            return await self.load_external(module_path, bot)
        except Exception:
            logger.exception(f"安装后加载插件失败: {module_path}")
            return False

    async def _fetch_plugin(
        self, source: str, name: str | None, plugins_dir: Path
    ) -> tuple[str, Path | None]:
        """拉取插件源码到 plugins/ 目录，返回 (插件名, 插件目录)"""
        import shutil
        import tempfile

        source = source.strip()
        is_local_path = Path(source).exists()
        temp_dir: str | None = None
        try:
            if is_local_path:
                src = Path(source).resolve()
                if src.is_dir():
                    return await self._copy_plugin_dir(src, name, plugins_dir)
                if src.is_file() and self._is_archive(src):
                    temp_dir = tempfile.mkdtemp(prefix="qb-plugin-")
                    extracted = await self._extract_archive(src, Path(temp_dir))
                    return await self._copy_plugin_dir(extracted, name, plugins_dir)
                logger.error(f"本地插件来源无效: {source}")
                return "", None
            else:
                # 远程来源：git 克隆或 HTTP 下载归档
                temp_dir = tempfile.mkdtemp(prefix="qb-plugin-")
                staging = Path(temp_dir) / "src"
                # git 仓库识别：git+ / git@ / ssh:// 前缀，或 .git 结尾的 http(s) URL
                is_git = source.startswith(("git+", "git@", "ssh://")) or (
                    source.startswith(("http://", "https://"))
                    and source.rstrip("/").endswith(".git")
                )
                if is_git:
                    repo = source[4:] if source.startswith("git+") else source
                    ok = await self._run_subprocess(
                        ["git", "clone", "--depth", "1", repo, str(staging)]
                    )
                    if not ok or not staging.exists():
                        logger.error(f"git 克隆失败: {source}")
                        return "", None
                elif source.startswith(("http://", "https://")):
                    ok = await self._download_archive(source, staging)
                    if not ok:
                        logger.error(f"下载归档失败: {source}")
                        return "", None
                    staging = await self._extract_archive(staging, staging.parent)
                else:
                    logger.error(f"不支持的插件来源: {source}")
                    return "", None

                # 找到插件目录（仓库根或其下含 plugin.json/__init__.py 的目录；
                # 指定 name 时优先精确匹配，兼容 plugins/<name>/ 嵌套布局）
                plugin_dir = self._locate_plugin_dir(staging, name)
                if plugin_dir is None:
                    logger.error(f"来源中未找到插件目录: {source}")
                    return "", None
                return await self._copy_plugin_dir(plugin_dir, name, plugins_dir)
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def _copy_plugin_dir(
        self, src: Path, name: str | None, plugins_dir: Path
    ) -> tuple[str, Path | None]:
        """复制插件目录到 plugins/，返回 (插件名, 目标目录)"""
        import shutil

        target_name = name or src.name
        target = plugins_dir / target_name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        try:
            shutil.copytree(
                src, target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv")
            )
            return target_name, target
        except OSError as e:
            logger.error(f"复制插件目录失败: {e}")
            return "", None

    @staticmethod
    async def _run_subprocess(cmd: list[str]) -> bool:
        """运行子进程并返回是否成功"""

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=NO_WINDOW_FLAG,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"子进程失败 ({cmd[0]}): {stdout.decode(errors='replace')[-2000:]}")
                return False
            return True
        except (OSError, ValueError) as e:
            logger.error(f"无法运行子进程 {cmd[0]}: {e}")
            return False

    @staticmethod
    async def _download_archive(url: str, dest: Path) -> bool:
        """下载远程归档文件到 dest"""
        import shutil
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Qingci-Bot-CE"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)
            return True
        except (OSError, urllib.error.URLError) as e:
            logger.error(f"下载失败 {url}: {e}")
            return False

    @staticmethod
    async def _extract_archive(archive: Path, dest_dir: Path) -> Path:
        """解压 zip/tar 归档，返回解压后的根目录"""
        import tarfile
        import zipfile

        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = archive.name.lower()
        if fname.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest_dir)
        elif fname.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
            with tarfile.open(archive) as tf:
                # Python 3.12+ 支持安全解压过滤，旧版本回退（来源通常可信）
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(dest_dir, filter="data")
                else:
                    tf.extractall(dest_dir)
        else:
            raise ValueError(f"不支持的归档格式: {fname}")
        # 若顶层为单个目录，返回该目录
        entries = [p for p in dest_dir.iterdir() if not p.name.startswith("__")]
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return dest_dir

    @staticmethod
    def _locate_plugin_dir(root: Path, name: str | None = None) -> Path | None:
        """定位仓库/归档中的插件目录

        1. 根目录本身是插件（含 plugin.json 或 __init__.py）→ 返回根
        2. 指定 name 时：递归（最多 2 层）查找目录名 == name 且是插件的目录
        3. 否则：返回首个直接子目录中的插件
        4. 直接子目录中找不到时，再向下找一层（兼容 plugins/<name>/ 嵌套）
        """
        if (root / "plugin.json").is_file() or (root / "__init__.py").is_file():
            return root

        def _is_plugin_dir(d: Path) -> bool:
            return (d / "plugin.json").is_file() or (d / "__init__.py").is_file()

        if name:
            for pattern in (f"*/{name}", f"*/*/{name}"):
                for child in root.glob(pattern):
                    if child.is_dir() and _is_plugin_dir(child):
                        return child

        for child in sorted(root.iterdir()):
            if child.is_dir() and _is_plugin_dir(child):
                return child

        # 嵌套一层：兼容 plugins/<name>/ 布局
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            for sub in sorted(child.iterdir()):
                if sub.is_dir() and _is_plugin_dir(sub):
                    return sub
        return None

    @staticmethod
    def _is_archive(path: Path) -> bool:
        return path.suffix.lower() in (".zip", ".tar", ".gz", ".tgz", ".bz2")

    # ---- 工具 ----

    def remove_temp_matcher(self, matcher: Matcher) -> None:
        """移除一次性（temp）匹配器并失效缓存"""
        if not getattr(matcher, "owner", ""):
            return
        plugin = self._plugins.get(matcher.owner)
        if plugin is not None and plugin.matchers and matcher in plugin.matchers:
            plugin.matchers.remove(matcher)
            self._invalidate_matchers_cache()


def _schema_from_annotations(config_cls: type) -> dict:
    """从普通类的类型注解兜底生成 JSON Schema（非 pydantic Config 类）"""
    props: dict[str, dict] = {}
    annotations = getattr(config_cls, "__annotations__", {}) or {}
    for field_name, ann in annotations.items():
        if field_name.startswith("_"):
            continue
        props[field_name] = {
            "title": field_name,
            "type": _annotation_json_type(ann),
        }
    return {"type": "object", "properties": props, "required": []}


def _annotation_json_type(ann: Any) -> str:
    """将 Python 类型注解映射为 JSON Schema 类型（兜底用）"""
    if ann is bool:
        return "boolean"
    if ann is int:
        return "integer"
    if ann is float:
        return "number"
    if ann is str:
        return "string"
    if ann is list:
        return "array"
    if ann is dict:
        return "object"
    origin = get_origin(ann)
    if origin is Union:
        args = [a for a in get_args(ann) if a is not type(None)]
        if args:
            return _annotation_json_type(args[0])
    return "string"
