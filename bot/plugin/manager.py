"""插件管理器"""

import asyncio
import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Optional

from .base import PluginBase
from .matcher import Matcher, begin_module_collection, end_module_collection

logger = logging.getLogger("qingci-bot.plugin.manager")


class PluginManager:
    """插件管理器：加载、卸载、热重载

    支持两种 Matcher 注册方式：
    1. 模块级装饰器 @on_command(...)：加载模块时自动收集
    2. 插件内 self.matchers.append(...)：在 on_load 中手动注册
    """

    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}  # name -> instance
        self._cached_matchers: Optional[list[Matcher]] = None

    @property
    def plugins(self) -> dict[str, PluginBase]:
        return self._plugins

    def get(self, name: str) -> Optional[PluginBase]:
        return self._plugins.get(name)

    def all_matchers(self) -> list[Matcher]:
        """收集所有插件的 Matcher（用于调度），结果已按优先级升序排序

        约定：priority 越小越先执行。返回缓存副本，防止调用方污染缓存。
        """
        if self._cached_matchers is None:
            result = []
            for plugin in self._plugins.values():
                if plugin.matchers:
                    result.extend(plugin.matchers)
            self._cached_matchers = sorted(result, key=lambda m: m.priority)
        return list(self._cached_matchers)

    def _invalidate_matchers_cache(self) -> None:
        """使 matcher 缓存失效"""
        self._cached_matchers = None

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
                # reload 前记录模块旧命名空间中已定义的插件类：
                # importlib.reload 在原命名空间重新执行代码、不清除旧属性，
                # 源码中已删除的类仍会残留在 module.__dict__ 中，
                # 需据此过滤出本次执行新定义的类
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
        """确保插件声明的依赖已加载（借鉴 NoneBot2 require 机制）

        - require 填写依赖插件 name；依赖已注册则跳过
        - 未注册时尝试加载 bot.plugin.builtin.<name> 模块
        - 依赖缺失或循环依赖时抛出 ValueError（插件加载失败）
        """
        for dep in plugin.require or []:
            if dep in self._plugins:
                continue
            if dep in loading:
                chain = " -> ".join([*loading, dep])
                raise ValueError(f"插件循环依赖: {chain}")
            dep_module = f"bot.plugin.builtin.{dep}"
            try:
                importlib.import_module(dep_module)
            except ImportError:
                raise ValueError(
                    f"插件 {plugin.name} 依赖的插件 {dep} 不存在"
                    f"（找不到模块 {dep_module}）"
                )
            await self._load_or_reload(dep_module, bot, _loading=loading)

    async def _register_from_module(
        self,
        module,
        collector: list[Matcher],
        bot,
        replaced_name: Optional[str] = None,
        stale_classes: Optional[set] = None,
        _loading: Optional[set] = None,
    ) -> None:
        """从模块中查找 PluginBase 子类并注册

        Args:
            stale_classes: reload 前模块中已存在的插件类集合；
                reload 后仅新定义/重新定义的类（不在该集合中）才视为有效，
                首次 load 传 None 不过滤
        """
        plugin_classes = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, PluginBase)
                and attr is not PluginBase
                and attr.__module__ == module.__name__  # 仅注册本模块定义的类
            ):
                plugin_classes.append(attr)

        if stale_classes is not None:
            # reload 场景：过滤掉旧命名空间残留的旧类对象（同一类重新执行后
            # 会生成新的类对象，身份不在旧集合中），仅保留本次新定义的类
            plugin_classes = [c for c in plugin_classes if c not in stale_classes]

        if not plugin_classes:
            # reload 路径：目标模块不再含插件类属于明确错误，不能静默成功；
            # 首次 load 路径保持原有语义（空模块直接跳过）
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

        # 依赖解析：递归加载依赖插件（检测缺失与循环依赖）。
        # loading 集合以插件 name 标识正在加载的插件（依赖解析用 name 匹配）
        loading = _loading if _loading is not None else set()
        if plugin.name in loading:
            chain = " -> ".join([*loading, plugin.name])
            raise ValueError(f"插件循环依赖: {chain}")
        loading.add(plugin.name)
        try:
            await self._ensure_dependencies(plugin, bot, loading)

            # reload 时插件可能改名：按原注册名查找/卸载旧实例，避免旧实例泄漏
            target_name = replaced_name or plugin.name
            old_plugin = self._plugins.get(target_name)
            # 先建后拆：先完整初始化新插件（含 on_load），失败时旧插件保持生效
            await self._init_plugin(plugin, bot)
            # on_load 中手动注册的 Matcher 未设置 owner，此处统一补齐
            # （保证 run_matchers 能定位到所属插件，mctx.plugin 可用）
            for m in plugin.matchers:
                if not m.owner:
                    m.owner = plugin.name
            # 关联模块级 Matcher（仅本模块定义的，避免跨模块 import 时误归属）
            for m in collector:
                handler_mod = getattr(m.handler, "__module__", "") or ""
                if handler_mod and handler_mod != module.__name__:
                    continue
                m.owner = plugin.name
                plugin.matchers.append(m)
            # 新插件就绪后再卸载旧插件并注册，避免插件真空；
            # 若此阶段被异常/取消打断，补偿调用新插件 on_unload 避免资源泄漏
            try:
                if old_plugin is not None:
                    await self.unload(target_name)
                # 不同模块定义同名插件时，后者覆盖前者属于配置错误，记录警告便于排查
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
                f" (matchers: {matcher_count})"
            )
            self._invalidate_matchers_cache()
        finally:
            loading.discard(plugin.name)

    async def unload(self, name: str) -> None:
        """卸载插件"""
        plugin = self._plugins.pop(name, None)
        if plugin:
            try:
                await plugin.on_unload()
            except Exception:
                logger.exception(f"插件 {name} on_unload 异常")
            # 兜底清理该插件注册的定时任务（job_id 带插件名前缀）；
            # bot/scheduler 可能为 None（如调度器未启用），需逐层守卫
            scheduler = getattr(plugin.bot, "scheduler", None) if plugin.bot else None
            if scheduler is not None:
                try:
                    scheduler.remove_jobs_by_owner(name)
                except Exception:
                    logger.exception(f"清理插件 {name} 定时任务异常")
            logger.info(f"插件已卸载: {name}")
        self._invalidate_matchers_cache()

    async def reload(self, name: str, bot) -> None:
        """重载插件"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"重载失败：插件 {name} 不存在")
            return

        # 重新加载模块（reload 会重新执行装饰器）
        # 先建后拆：_register_from_module 会在新插件 on_load 成功后才卸载旧插件，
        # 新插件加载失败时旧插件继续生效；
        # replaced_name 传入原注册名，防止插件改名后旧实例泄漏
        module_path = type(plugin).__module__
        try:
            await self._load_or_reload(module_path, bot, replaced_name=name)
        except Exception:
            logger.exception(f"重载插件 {name} 失败，旧插件保持生效")
            raise
        finally:
            self._invalidate_matchers_cache()

    async def _init_plugin(self, plugin: PluginBase, bot) -> None:
        """初始化插件依赖"""
        plugin.bot = bot
        plugin.db = bot.db
        plugin.config = bot.config
        plugin.connection = bot.connection
        plugin.llm = bot.llm
        # 可选依赖注入（允许为 None，由后续批次创建真实例）
        plugin.scheduler = bot.scheduler          # 批次 1：定时任务调度器
        plugin.tool_registry = bot.tool_registry  # 批次 3：Function Calling 工具注册表
        plugin.knowledge_store = bot.knowledge_store  # 批次 3：知识库向量存储
        plugin.matchers = []  # 初始化 Matcher 列表
        await plugin.on_load()

    async def shutdown(self) -> None:
        """关闭所有插件"""
        for name in list(self._plugins.keys()):
            await self.unload(name)
