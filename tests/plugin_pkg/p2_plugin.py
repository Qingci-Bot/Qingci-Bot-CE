"""P2 细粒度事件处理钩子测试插件

验证两项 P2 能力：
1. run_preprocessor：Matcher 运行前全局钩子（本插件提供 /ping 命令）
2. on_calling_api：平台接口调用钩子（本插件提供 /poke 命令触发 API 调用）
"""

from bot.plugin.base import PluginBase
from bot.plugin.matcher import MatcherContext, on_command


@on_command("ping", description="测试 Matcher 运行前钩子")
async def ping(ctx: MatcherContext) -> str:
    return "pong"


@on_command("poke", description="触发平台 API 调用（测试 on_calling_api 钩子）")
async def poke(ctx: MatcherContext) -> str:
    await ctx.plugin.connection.call_api("get_group_info", {"group_id": 20001})
    return "done"


class P2Plugin(PluginBase):
    name = "p2"
    version = "1.0.0"
    description = "P2 细粒度事件处理钩子测试插件"

    async def on_load(self):
        return None

    async def on_unload(self):
        return None
