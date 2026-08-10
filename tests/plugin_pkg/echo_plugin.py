"""示例插件：用于验证 bot.testing 测试工具

覆盖能力：
- 命令回复
- 多轮会话状态
- 主动发消息（connection.send_msg）
- 管理员权限命令
"""

from typing import Optional

from bot.plugin.base import PluginBase
from bot.plugin.matcher import MatcherContext, on_command, on_message
from bot.plugin.permission import SUPERUSER


class EchoPlugin(PluginBase):
    name = "echo"
    version = "1.0.0"
    description = "测试工具示例插件"

    async def on_load(self):
        self.matchers.append(
            on_command("ping")(self._ping)
        )
        self.matchers.append(
            on_command("register")(self._register)
        )
        # 会话续接：处于注册流程中时，任意文本进入下一步
        self.matchers.append(
            on_message(priority=5, block=True)(self._register_continue)
        )
        self.matchers.append(
            on_command("notify", permission=SUPERUSER)(self._notify)
        )

    async def on_unload(self):
        pass

    async def _ping(self, ctx: MatcherContext) -> str:
        return "pong"

    async def _register_continue(self, ctx: MatcherContext) -> Optional[str]:
        """注册流程续接：step 非 start 时处理输入文本"""
        step = ctx.session_state.get("step", "start")
        if step == "start":
            return None  # 未在注册流程，放行
        return await self._register(ctx)

    async def _register(self, ctx: MatcherContext) -> str:
        step = ctx.session_state.get("step", "start")
        if step == "start":
            ctx.session_state.set("step", "waiting_name", ttl=300)
            return "请输入你的名字："
        if step == "waiting_name":
            name = ctx.plain_text
            ctx.session_state.set("name", name, ttl=300)
            ctx.session_state.set("step", "waiting_age", ttl=300)
            return f"你好 {name}，请输入你的年龄："
        if step == "waiting_age":
            age = ctx.plain_text
            name = ctx.session_state.get("name")
            ctx.session_state.delete("step")
            return f"注册完成！{name}，{age}岁"
        return "注册流程已结束"

    async def _notify(self, ctx: MatcherContext) -> str:
        # 主动发消息（不通过返回值）
        await ctx.bot.connection.send_group_msg(ctx.group_id, "管理员通知已发送")
        return "已发送"