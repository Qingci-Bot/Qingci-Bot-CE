"""依赖插件：被 dep_plugin 声明 require"""

from bot.plugin.base import PluginBase


class DepPlugin(PluginBase):
    name = "dep"
    version = "1.0.0"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass
