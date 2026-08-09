"""声明依赖的插件（require=["dep"]）"""

from bot.plugin.base import PluginBase


class DepConsumer(PluginBase):
    name = "dep_consumer"
    version = "1.0.0"
    require = ["dep"]

    async def on_load(self):
        self._dep = self.bot.plugin_manager.get("dep")

    async def on_unload(self):
        pass
