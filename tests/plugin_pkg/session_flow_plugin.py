"""会话阶梯（多轮交互）测试插件

演示 Session 阶梯 API：
- pause()：发送文本并挂起，等待同会话下一条消息续接同一 handler
- finish()：发送文本并结束阶梯
- reject()：发送文本，拒绝当前输入并继续等待
- session 自定义属性跨轮保留（每轮 pause 前先推进 step 状态）

阶梯的提示文本统一走主动发送通道（connection），
handler 正常 return 的文本走回复通道。
"""

from bot.plugin.base import PluginBase
from bot.plugin.matcher import MatcherContext, on_command


class SessionFlowPlugin(PluginBase):
    name = "session_flow"
    version = "1.0.0"
    description = "会话阶梯测试插件"

    async def on_load(self):
        self.matchers.append(on_command("wizard")(self._wizard))
        self.matchers.append(on_command("survey")(self._survey))
        self.matchers.append(on_command("validated")(self._validated))

    async def on_unload(self):
        pass

    async def _wizard(self, ctx: MatcherContext) -> None:
        """向导：名字 -> 年龄 -> 完成（session 属性跨轮保留）"""
        step = getattr(ctx.session, "step", "ask_name")
        if step == "ask_name":
            ctx.session.step = "ask_age"
            await ctx.session.pause("请输入你的名字：")
        if step == "ask_age":
            ctx.session.name = ctx.plain_text
            ctx.session.step = "done"
            await ctx.session.pause(f"你好 {ctx.session.name}，请输入你的年龄：")
        ctx.session.age = ctx.plain_text
        await ctx.session.finish(f"向导完成：{ctx.session.name}，{ctx.session.age}岁")

    async def _survey(self, ctx: MatcherContext) -> None:
        """问卷：拒绝非数字输入，直到收到数字才结束"""
        if getattr(ctx.session, "step", "ask_score") == "ask_score":
            ctx.session.step = "read_score"
            await ctx.session.pause("请给本次服务打分（1-10）：")
        try:
            score = int(ctx.plain_text)
        except ValueError:
            await ctx.session.reject("输入无效，请输入数字：")
            return
        await ctx.session.finish(f"感谢评分：{score} 分")

    async def _validated(self, ctx: MatcherContext) -> None:
        """校验型阶梯：必须回答 yes 才能结束，否则拒绝继续等"""
        if getattr(ctx.session, "step", "confirm") == "confirm":
            ctx.session.step = "verify"
            await ctx.session.pause("确认删除全部数据？回复 yes 继续：")
        if ctx.plain_text.strip().lower() != "yes":
            await ctx.session.reject("请回复 yes 确认：")
            return
        await ctx.session.finish("已确认，数据已删除。")
