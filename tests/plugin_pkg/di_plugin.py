"""P0 功能测试插件：参数级依赖注入 + 全局生命周期钩子"""

from bot.core.di import Depends
from bot.core.dispatcher import MessageDispatcher
from bot.core.session_state import SessionStateManager
from bot.plugin.base import PluginBase
from bot.plugin.matcher import MatcherContext, on_command


@on_command("di")
async def _di(
    ctx: MatcherContext,
    state: SessionStateManager = Depends(SessionStateManager),
    dispatcher: MessageDispatcher = None,
) -> str:
    return f"{type(state).__name__}-{type(dispatcher).__name__}"


class DiPlugin(PluginBase):
    name = "di"
    version = "1.0.0"
    description = "DI 与生命周期测试插件"

    def __init__(self):
        super().__init__()
        self.events: list[str] = []

    async def on_load(self):
        self.events.append("load")

    async def on_unload(self):
        self.events.append("unload")

    async def on_startup(self):
        self.events.append("startup")

    async def on_shutdown(self):
        self.events.append("shutdown")

    async def on_bot_connect(self):
        self.events.append("connect")

    async def on_metaevent(self, event: dict) -> bool | None:
        self.events.append(f"meta:{event.get('meta_event_type')}")
        return None
