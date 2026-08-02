"""插件管理器"""

import importlib
import logging
import pkgutil
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
            try:
                # 开启模块级 Matcher 收集
                collector = begin_module_collection()
                module = importlib.import_module(
                    f".builtin.{module_info.name}", package="bot.plugin"
                )
                end_module_collection()

                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, PluginBase)
                        and attr is not PluginBase
                    ):
                        plugin = attr()
                        await self._init_plugin(plugin, bot)
                        # 关联模块级 Matcher
                        for m in collector:
                            m.owner = plugin.name
                            plugin.matchers.append(m)
                        self._plugins[plugin.name] = plugin
                        matcher_count = len(plugin.matchers) if plugin.matchers else 0
                        logger.info(
                            f"内置插件已加载: {plugin.name} v{plugin.version}"
                            f" (matchers: {matcher_count})"
                        )
            except Exception:
                logger.exception(f"加载内置插件失败: {module_info.name}")

    async def load_external(self, module_path: str, bot) -> bool:
        """加载外部插件"""
        try:
            collector = begin_module_collection()
            module = importlib.import_module(module_path)
            end_module_collection()

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
                    logger.info(f"外部插件已加载: {plugin.name} v{plugin.version}")
                    return True
        except Exception:
            logger.exception(f"加载外部插件失败: {module_path}")
            return False
        return False

    async def unload(self, name: str):
        """卸载插件"""
        plugin = self._plugins.pop(name, None)
        if plugin:
            await plugin.on_unload()
            logger.info(f"插件已卸载: {name}")

    async def reload(self, name: str, bot):
        """重载插件"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"重载失败：插件 {name} 不存在")
            return
        await plugin.on_unload()
        module_path = type(plugin).__module__
        module = importlib.import_module(module_path)

        collector = begin_module_collection()
        module = importlib.reload(module)
        end_module_collection()

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, PluginBase)
                and attr is not PluginBase
            ):
                new_plugin = attr()
                await self._init_plugin(new_plugin, bot)
                # 关联模块级 Matcher
                for m in collector:
                    m.owner = new_plugin.name
                    new_plugin.matchers.append(m)
                # 使用新插件的实际 name 作为 key
                self._plugins.pop(name, None)
                self._plugins[new_plugin.name] = new_plugin
                logger.info(f"插件已重载: {new_plugin.name}")
                return

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