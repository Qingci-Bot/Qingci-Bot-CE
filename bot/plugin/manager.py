"""插件管理器"""

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Optional

from .base import PluginBase

logger = logging.getLogger("qingci-bot.plugin.manager")


class PluginManager:
    """插件管理器：加载、卸载、热重载"""

    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}  # name -> instance

    @property
    def plugins(self) -> dict[str, PluginBase]:
        return self._plugins

    def get(self, name: str) -> Optional[PluginBase]:
        return self._plugins.get(name)

    async def load_builtin(self, bot):
        """加载内置插件"""
        from . import builtin
        pkg_path = Path(builtin.__path__[0])
        for module_info in pkgutil.iter_modules([str(pkg_path)]):
            if module_info.name.startswith("_"):
                continue
            try:
                module = importlib.import_module(f".builtin.{module_info.name}", package="bot.plugin")
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, PluginBase)
                        and attr is not PluginBase
                    ):
                        plugin = attr()
                        await self._init_plugin(plugin, bot)
                        self._plugins[plugin.name] = plugin
                        logger.info(f"内置插件已加载: {plugin.name} v{plugin.version}")
            except Exception:
                logger.exception(f"加载内置插件失败: {module_info.name}")

    async def load_external(self, module_path: str, bot) -> bool:
        """加载外部插件"""
        try:
            module = importlib.import_module(module_path)
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
        module = importlib.reload(module)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, PluginBase)
                and attr is not PluginBase
            ):
                new_plugin = attr()
                await self._init_plugin(new_plugin, bot)
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
        await plugin.on_load()

    async def shutdown(self):
        """关闭所有插件"""
        for name in list(self._plugins.keys()):
            await self.unload(name)