"""插件管理器"""

import asyncio
import importlib
import json
import logging
import pkgutil
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from packaging.version import Version, InvalidVersion
from packaging.specifiers import SpecifierSet

from .base import PluginBase, PluginStatus
from .matcher import Matcher, begin_module_collection, end_module_collection

logger = logging.getLogger("qingci-bot.plugin.manager")


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


def _parse_version_spec(dep_spec: str) -> tuple[str, Optional[SpecifierSet]]:
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
            raise ValueError(f"无效的版本约束: {dep_spec}")
    return name, None


def _load_plugin_json(directory: Path) -> Optional[dict]:
    """从目录中加载 plugin.json 元数据（若存在）"""
    json_path = directory / "plugin.json"
    if not json_path.is_file():
        return None
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning(f"plugin.json 解析失败: {json_path}")
        return None


class PluginManager:
    """插件管理器：加载、卸载、热重载、状态管理、指标监控

    支持两种 Matcher 注册方式：
    1. 模块级装饰器 @on_command(...)：加载模块时自动收集
    2. 插件内 self.matchers.append(...)：在 on_load 中手动注册
    """

    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}
        self._cached_matchers: Optional[list[Matcher]] = None
        # Matcher 执行指标: owner_name -> Matcher 实例 -> MatcherMetrics
        self._metrics: dict[str, dict[Matcher, MatcherMetrics]] = {}
        # plugin.json 预取缓存: module_path -> metadata dict
        self._metadata_cache: dict[str, dict] = {}

    @property
    def plugins(self) -> dict[str, PluginBase]:
        return self._plugins

    def get(self, name: str) -> Optional[PluginBase]:
        return self._plugins.get(name)

    def all_matchers(self) -> list[Matcher]:
        """收集所有已启用插件的 Matcher（用于调度），结果已按优先级升序排序

        约定：priority 越小越先执行。返回缓存副本，防止调用方污染缓存。
        仅 LOADED 状态的插件参与调度。
        """
        if self._cached_matchers is None:
            result = []
            for plugin in self._plugins.values():
                if plugin.matchers and plugin.status == PluginStatus.LOADED:
                    result.extend(plugin.matchers)
            self._cached_matchers = sorted(result, key=lambda m: m.priority)
        return list(self._cached_matchers)

    def _invalidate_matchers_cache(self) -> None:
        self._cached_matchers = None

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
        for matcher in (plugin.matchers or []):
            owner_metrics = self._metrics.get(plugin_name, {})
            m = owner_metrics.get(matcher)
            if m is None:
                continue
            handler_name = getattr(matcher.handler, "__name__", "unknown")
            result.append({
                "handler": handler_name,
                "event_type": matcher.event_type,
                "priority": matcher.priority,
                "description": matcher.description,
                "call_count": m.call_count,
                "avg_time_ms": round(m.avg_time_ms, 2),
                "total_time_ms": round(m.total_time_ms, 2),
                "error_count": m.error_count,
                "last_call_time": m.last_call_time,
            })
        return result

    # ---- 元数据发现 ----

    def discover_metadata(self, directory: Path) -> list[dict]:
        """扫描目录中的 plugin.json 元数据，无需导入模块"""
        results = []
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
        """加载内置插件"""
        from . import builtin
        pkg_path = Path(builtin.__path__[0])
        for module_info in pkgutil.iter_modules([str(pkg_path)]):
            if module_info.name.startswith("_"):
                continue
            full_path = f"bot.plugin.builtin.{module_info.name}"
            try:
                await self._load_or_reload(full_path, bot)
            except Exception:
                logger.exception(f"加载内置插件失败: {module_info.name}")

    async def load_external(self, module_path: str, bot) -> bool:
        """加载外部插件"""
        try:
            await self._load_or_reload(module_path, bot)
            return True
        except Exception:
            logger.exception(f"加载外部插件失败: {module_path}")
            return False

    async def load_external_dir(self, bot, directory: Optional[Path] = None) -> int:
        """扫描并加载外部插件目录（plugins/ 下的 .py 文件）

        源码模式与 frozen（exe）模式均支持：exe 所在目录下创建
        plugins/ 目录，放入 .py 文件即可自动加载。

        Args:
            bot: Bot 实例
            directory: 插件目录路径，默认为 app_root()/plugins/

        Returns:
            成功加载的插件数量
        """
        if directory is None:
            from ..paths import app_root
            directory = app_root() / "plugins"

        root_str = str(directory.parent)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(f"无法创建插件目录: {directory}，跳过外部插件加载")
            return 0
        init_file = directory / "__init__.py"
        if not init_file.exists():
            try:
                init_file.write_text(
                    "# Qingci-Bot CE 外部插件目录\n"
                    "# 将 .py 插件文件放入此目录即可自动加载\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.warning(f"无法写入 {init_file}，跳过外部插件加载")
                return 0

        count = 0
        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = py_file.stem
            module_path = f"plugins.{module_name}"
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
        self, full_path: str, bot, replaced_name: Optional[str] = None,
        _loading: Optional[set] = None,
    ) -> None:
        """加载或重载模块，确保模块级装饰器重新执行

        对已缓存的模块使用 reload，对新模块使用 import_module。
        始终包裹 begin/end collection 以收集模块级 Matcher。

        Args:
            replaced_name: reload 场景下被替换插件的原注册名（插件可能改名，
                首次 load 传 None）
            _loading: 正在加载的模块名集合（依赖解析用，检测循环依赖）
        """
        collector = begin_module_collection()
        stale_classes: Optional[set] = None
        try:
            if full_path in sys.modules:
                module = sys.modules[full_path]
                stale_classes = {
                    attr
                    for attr in vars(module).values()
                    if isinstance(attr, type)
                    and issubclass(attr, PluginBase)
                    and attr is not PluginBase
                    and attr.__module__ == module.__name__
                }
                module = importlib.reload(module)
            else:
                module = importlib.import_module(full_path)
        finally:
            end_module_collection()

        await self._register_from_module(
            module, collector, bot, replaced_name, stale_classes, _loading
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
                        )
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
                    f"插件 {plugin.name} 依赖的插件 {dep_name} 不存在"
                    f"（找不到模块 {dep_module}）"
                )
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
                        )
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
        replaced_name: Optional[str] = None,
        stale_classes: Optional[set] = None,
        _loading: Optional[set] = None,
    ) -> None:
        """从模块中查找 PluginBase 子类并注册"""
        plugin_classes = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, PluginBase)
                and attr is not PluginBase
                and attr.__module__ == module.__name__
            ):
                plugin_classes.append(attr)

        if stale_classes is not None:
            plugin_classes = [c for c in plugin_classes if c not in stale_classes]

        if not plugin_classes:
            if replaced_name is not None:
                raise ValueError(
                    f"模块 {module.__name__} 中未找到插件类，"
                    f"无法替换已注册的插件 {replaced_name}"
                )
            return

        if len(plugin_classes) > 1:
            raise ValueError(
                f"模块 {module.__name__} 定义了 {len(plugin_classes)} 个 PluginBase 子类，"
                f"每模块仅允许 1 个"
            )

        plugin_cls = plugin_classes[0]
        plugin = plugin_cls()

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
                logger.warning(
                    f"插件 {plugin.name} 配置校验失败: {e}，使用原始 dict"
                )
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
        plugin.matchers = plugin.matchers or []
        plugin._status = PluginStatus.LOADING
        await plugin.on_load()

    # ---- 关闭 ----

    async def shutdown(self) -> None:
        """关闭所有插件"""
        for name in list(self._plugins.keys()):
            await self.unload(name)

    # ---- 工具 ----

    def remove_temp_matcher(self, matcher: Matcher) -> None:
        """移除一次性（temp）匹配器并失效缓存"""
        if not getattr(matcher, "owner", ""):
            return
        plugin = self._plugins.get(matcher.owner)
        if plugin is not None and plugin.matchers and matcher in plugin.matchers:
            plugin.matchers.remove(matcher)
            self._invalidate_matchers_cache()