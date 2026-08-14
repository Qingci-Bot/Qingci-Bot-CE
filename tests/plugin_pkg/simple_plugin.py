"""简单插件：模块级装饰器 + 插件内注册两种方式"""

from bot.plugin.base import PluginBase
from bot.plugin.matcher import MatcherContext, on_command, on_message
from bot.plugin.rule import keyword


@on_command("modping")
async def _mod_ping(ctx: MatcherContext) -> str:
    return "mod pong"


class SimplePlugin(PluginBase):
    name = "simple"
    version = "1.0.0"
    description = "测试插件"

    async def on_load(self):
        self.matchers.append(on_command("ping")(self._ping))
        self.matchers.append(on_message(rule=keyword("天气"))(self._weather))

    async def on_unload(self):
        pass

    async def _ping(self, ctx: MatcherContext) -> str:
        return "pong"

    async def _weather(self, ctx: MatcherContext) -> str:
        return "今天晴"
