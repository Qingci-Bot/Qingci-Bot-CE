"""on_load 运行时注册子命令的插件（模拟 shiguang 的注册方式）

模块级收集器在 import 阶段已关闭，on_command 的子指令 matcher 只能经
parent.meta["sub_matchers"] 随 parent 传递，由 PluginManager 展开注册。
"""

from bot.plugin.base import PluginBase
from bot.plugin.matcher import MatcherContext, on_command


class SubcmdRuntimePlugin(PluginBase):
    name = "subcmd_runtime"

    async def on_load(self):
        self.matchers.append(
            on_command(
                "排行",
                description="排行",
                subcommands={
                    "今日": self._ranking_today,
                    "月榜": self._ranking_month,
                },
            )(self._noop)
        )

    async def on_unload(self):
        pass

    async def _noop(self, _ctx: MatcherContext):
        return None

    async def _ranking_today(self, _ctx: MatcherContext):
        return "今日排行"

    async def _ranking_month(self, _ctx: MatcherContext):
        return "月榜排行"
