"""P1 跨插件事件总线消费测试插件

订阅 "custom.event"，由 p1_plugin 的 /broadcast 命令发布。
"""

from bot.plugin.base import PluginBase


class P1BusConsumer(PluginBase):
    name = "p1_bus"
    version = "1.0.0"
    description = "事件总线跨插件消费插件"

    def __init__(self):
        super().__init__()
        self.received: dict | None = None

    async def on_load(self):
        await self.event_bus.subscribe("custom.event", self._on_event)

    async def _on_event(self, event_type: str, data: dict) -> None:
        self.received = data

    async def on_unload(self):
        return None
