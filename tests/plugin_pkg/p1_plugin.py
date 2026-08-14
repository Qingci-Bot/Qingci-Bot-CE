"""P1 功能测试插件：指令系统增强 + LLM 工具 + 配置 schema + 事件总线发布

验证四项 P1 能力：
1. 指令系统增强：别名、类型化参数（args_schema）、子指令
2. 插件级 LLM 工具声明（@llm_tool）
3. 配置 schema 自动生成（Config 内嵌 pydantic 类）
4. 事件总线发布（@on_command 内 publish 到跨插件主题）
"""

from pydantic import BaseModel

from bot.plugin.base import PluginBase
from bot.plugin.llm_tool import llm_tool
from bot.plugin.matcher import MatcherContext, on_command

# ---- 指令增强：别名 + 类型化参数 ----


@on_command(
    "weather",
    aliases=("天气",),
    args_schema={"city": str, "days": int},
    description="查询天气（city, days）",
)
async def weather(ctx: MatcherContext, city: str = "", days: int = 1) -> str:
    return f"{city}:{days}天"


# ---- 指令增强：子指令 ----


async def _ban(ctx: MatcherContext) -> str:
    return f"ban:{ctx.args}"


async def _unban(ctx: MatcherContext) -> str:
    return f"unban:{ctx.args}"


@on_command("admin", subcommands={"ban": _ban, "unban": _unban}, description="管理子指令")
async def admin(ctx: MatcherContext) -> str:
    return "子指令: ban/unban"


# ---- 插件级 LLM 工具声明 ----


@llm_tool(description="计算两个整数之和")
def tool_add(a: int, b: int) -> int:
    return a + b


# ---- 事件总线：发布到跨插件主题 ----


@on_command("broadcast", description="发布跨插件事件")
async def broadcast(ctx: MatcherContext) -> str:
    await ctx.plugin.event_bus.publish("custom.event", text=ctx.args)
    return "ok"


class P1Plugin(PluginBase):
    name = "p1"
    version = "1.0.0"
    description = "P1 功能测试插件"

    class Config(BaseModel):
        greeting: str = "你好"
        retries: int = 3
        verbose: bool = False

    def __init__(self):
        super().__init__()
        self.received: dict | None = None

    async def on_load(self):
        await self.event_bus.subscribe("p1.event", self._on_event)

    async def _on_event(self, event_type: str, data: dict) -> None:
        self.received = data

    async def on_unload(self):
        return None
