"""插件管理器"""

import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Optional

from .base import PluginBase
from .matcher import begin_module_collection, end_module_collection

logger = logging.getLogger("qingci-bot.plugin.manager")


class PluginManager:
    """插件管理器：加载、卸载、热重载

    支持两种 Matcher 注册方式：
    1. 模块级装饰器 @on_command(...)：加载模块时自动收集
    2. 插件内 self.matchers.append(...)：在 on_load 中手动注册
    """

    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}  # name -> instance

    @property
    def plugins(self) -> dict[str, PluginBase]:
        return self._plugins

    def get(self, name: str) -> Optional[PluginBase]:
        return self._plugins.get(name)

    def all_matchers(self) -> list:
        """收集所有插件的 Matcher（用于调度）"""
        result = []
        for plugin in self._plugins.values():
            if plugin.matchers:
                result.extend(plugin.matchers)
        return result

    async def load_builtin(self, bot):
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

    async def _load_or_reload(self, full_path: str, bot):
        """加载或重载模块，确保模块级装饰器重新执行

        对已缓存的模块使用 reload，对新模块使用 import_module。
        始终包裹 begin/end collection 以收集模块级 Matcher。
        """
        collector = begin_module_collection()
        try:
            if full_path in sys.modules:
                module = importlib.reload(sys.modules[full_path])
            else:
                module = importlib.import_module(full_path)
        finally:
            end_module_collection()

        await self._register_from_module(module, collector, bot)

    async def _register_from_module(self, module, collector: list, bot):
        """从模块中查找 PluginBase 子类并注册"""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, PluginBase)
                and attr is not PluginBase
            ):
                plugin = attr()
                # 同名插件先卸载
                if plugin.name in self._plugins:
                    await self.unload(plugin.name)
                await self._init_plugin(plugin, bot)
                # 关联模块级 Matcher
                for m in collector:
                    m.owner = plugin.name
                    plugin.matchers.append(m)
                self._plugins[plugin.name] = plugin
                matcher_count = len(plugin.matchers) if plugin.matchers else 0
                logger.info(
                    f"插件已加载: {plugin.name} v{plugin.version}"
                    f" (matchers: {matcher_count})"
                )

    async def unload(self, name: str):
        """卸载插件"""
        plugin = self._plugins.pop(name, None)
        if plugin:
            try:
                await plugin.on_unload()
            except Exception:
                logger.exception(f"插件 {name} on_unload 异常")
            logger.info(f"插件已卸载: {name}")

    async def reload(self, name: str, bot):
        """重载插件"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"重载失败：插件 {name} 不存在")
            return

        # 先卸载旧插件
        await self.unload(name)

        # 重新加载模块（reload 会重新执行装饰器）
        module_path = type(plugin).__module__
        try:
            await self._load_or_reload(module_path, bot)
        except Exception:
            logger.exception(f"重载插件 {name} 失败")
            # 旧插件已卸载，新插件加载失败，不残留僵尸状态
            raise

    async def _init_plugin(self, plugin: PluginBase, bot):
        """初始化插件依赖"""
        plugin.bot = bot
        plugin.db = bot.db
        plugin.config = bot.config
        plugin.connection = bot.connection
        plugin.llm = bot.llm
        plugin.matchers = []  # 初始化 Matcher 列表
        await plugin.on_load()

    async def shutdown(self):
        """关闭所有插件"""
        for name in list(self._plugins.keys()):
            await self.unload(name)
