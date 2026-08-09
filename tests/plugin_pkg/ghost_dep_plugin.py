"""声明了不存在依赖的插件（require=["ghost_dep"]，应加载失败）"""

from bot.plugin.base import PluginBase


class GhostDepConsumer(PluginBase):
    name = "ghost_consumer"
    version = "1.0.0"
    require = ["ghost_dep"]

    async def on_load(self):
        pass

    async def on_unload(self):
        pass
