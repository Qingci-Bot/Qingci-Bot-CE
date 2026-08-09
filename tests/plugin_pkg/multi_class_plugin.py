"""定义了多个 PluginBase 子类的模块（应被拒绝加载）"""

from bot.plugin.base import PluginBase


class FirstPlugin(PluginBase):
    name = "first"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass


class SecondPlugin(PluginBase):
    name = "second"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass
