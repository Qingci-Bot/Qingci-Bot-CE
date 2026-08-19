"""Matcher 与一次性匹配器（temp）自动移除测试

通过真实 Dispatcher.run_matchers 链路验证：
- MatcherContext 字段注入
- temp 匹配器执行后自动从插件移除（不再响应第二次）
- 普通匹配器保留
- block/priority 语义
"""

from bot.plugin.base import PluginBase, PluginStatus
from bot.plugin.matcher import Matcher, MatcherContext, on_command, on_message
from bot.plugin.rule import Rule, keyword


class RecordingPlugin(PluginBase):
    """记录 handler 调用痕迹的测试插件"""

    name = "recording"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        self.calls = []

    async def on_load(self):
        pass

    async def on_unload(self):
        pass


def make_event(plain_text="", message_type="private", user_id=10001):
    """构造 OneBot v11 消息事件"""
    return {
        "post_type": "message",
        "message_type": message_type,
        "message_id": "test-1",
        "user_id": user_id,
        "self_id": 20002,
        "raw_message": plain_text,
        "message": [{"type": "text", "data": {"text": plain_text}}],
    }


def register_plugin(bot, plugin):
    """将测试插件注册进 bot 的 PluginManager（模拟 _init_plugin 后的状态）"""
    plugin._status = PluginStatus.LOADED
    for m in plugin.matchers or []:
        if not m.owner:
            m.owner = plugin.name
    bot.plugin_manager._plugins[plugin.name] = plugin
    bot.plugin_manager._invalidate_matchers_cache()
    return plugin


async def run(bot, plain_text, message_type="private", user_id=10001):
    """dispatch + run_matchers 完整链路，返回 (reply, blocked)"""
    from bot.core.dispatcher import MessageDispatcher

    dispatcher = MessageDispatcher()
    event = make_event(plain_text, message_type, user_id)
    ctx = dispatcher.dispatch(event)
    return await dispatcher.run_matchers(bot, event, ctx)


class TestMatcherDispatch:
    async def test_command_match_and_ctx(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx: MatcherContext):
            plugin.calls.append((ctx.command, ctx.args))
            return "pong"

        plugin.matchers = [on_command("ping")(handler)]
        register_plugin(bot, plugin)

        reply, blocked = await run(bot, "/ping 你好")
        assert reply == "pong"
        assert blocked is True
        assert plugin.calls == [("ping", "你好")]

    async def test_keyword_match(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx):
            return "晴"

        plugin.matchers = [on_message(rule=keyword("天气"))(handler)]
        register_plugin(bot, plugin)

        reply, _ = await run(bot, "今天天气如何")
        assert reply == "晴"

    async def test_no_match(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx):
            return "pong"

        plugin.matchers = [on_command("ping")(handler)]
        register_plugin(bot, plugin)

        reply, blocked = await run(bot, "其他内容")
        assert reply is None
        assert blocked is False

    async def test_priority_order(self, bot):
        plugin = RecordingPlugin()
        order = []

        async def low(ctx):
            order.append("low")
            return None  # 不回复，继续分发

        async def high(ctx):
            order.append("high")
            return "high-reply"

        # 高优先级先执行并阻塞（返回回复）
        plugin.matchers = [
            on_command("cmd", priority=10, block=True)(low),
            on_command("cmd", priority=1, block=True)(high),
        ]
        register_plugin(bot, plugin)

        reply, blocked = await run(bot, "/cmd")
        assert reply == "high-reply"
        assert blocked is True
        assert order == ["high"]  # 低优先级未执行

    async def test_block_false_continues(self, bot):
        plugin = RecordingPlugin()

        async def first(ctx):
            return None

        async def second(ctx):
            return "second-reply"

        plugin.matchers = [
            on_command("cmd", priority=1, block=False)(first),
            on_command("cmd", priority=2, block=True)(second),
        ]
        register_plugin(bot, plugin)

        reply, blocked = await run(bot, "/cmd")
        assert reply == "second-reply"
        assert blocked is True


class TestTempMatcherAutoRemove:
    async def test_temp_removed_after_first_use(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx):
            plugin.calls.append("triggered")
            return "once"

        m = on_command("once", temp=True)(handler)
        plugin.matchers = [m]
        register_plugin(bot, plugin)

        # 第一次触发：命中并自动移除
        reply1, _ = await run(bot, "/once")
        assert reply1 == "once"
        assert plugin.calls == ["triggered"]
        assert m not in plugin.matchers
        assert len(plugin.matchers) == 0

        # 第二次触发：不再命中（已移除）
        reply2, blocked = await run(bot, "/once")
        assert reply2 is None
        assert blocked is False
        assert plugin.calls == ["triggered"]

    async def test_non_temp_kept(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx):
            return "keep"

        m = on_command("keep")(handler)
        plugin.matchers = [m]
        register_plugin(bot, plugin)

        for _ in range(2):
            reply, _ = await run(bot, "/keep")
            assert reply == "keep"
        assert m in plugin.matchers
        assert len(plugin.matchers) == 1

    async def test_temp_removed_even_when_handler_returns_none(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx):
            return None  # 未回复也应移除

        m = on_command("silent", temp=True, block=False)(handler)
        plugin.matchers = [m]
        register_plugin(bot, plugin)

        reply, _ = await run(bot, "/silent")
        assert reply is None
        assert m not in plugin.matchers


class TestEventMatcherTypePreservation:
    """验证事件 Matcher（request/notice）的返回值类型保留

    修复：request Matcher 返回的 bool 审批结果不应被 str() 化，
    否则 bot.py 的 isinstance(result, (bool, int)) 判断会失败，
    导致审批被静默丢弃。
    """

    @staticmethod
    def make_request_event(request_type: str = "friend", user_id: int = 20001, comment: str = ""):
        """构造 OneBot v11 请求事件"""
        return {
            "post_type": "request",
            "request_type": request_type,
            "user_id": user_id,
            "comment": comment,
            "flag": "test-flag-1",
            "self_id": 20002,
        }

    @staticmethod
    def make_event_matcher(handler, event_type: str):
        """构造指定事件类型的 Matcher（无公开工厂，直接构建）"""
        return Matcher(
            handler=handler,
            rule=Rule(),  # 空规则 = 恒匹配
            priority=1,
            block=True,
            event_type=event_type,
        )

    async def test_request_matcher_bool_approved(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx: MatcherContext):
            return True  # 审批通过

        plugin.matchers = [self.make_event_matcher(handler, "request")]
        register_plugin(bot, plugin)

        event = self.make_request_event()
        ctx = bot.dispatcher.dispatch(event)
        reply, _ = await bot.dispatcher._run_event_matchers(bot, event, ctx)

        # 修复前：reply 是 "True"（字符串），approve 被静默丢弃
        # 修复后：reply 是 True（bool），可被正确识别
        assert reply is True, f"reply 应保留 bool 类型 True，实际为 {type(reply)}: {reply!r}"

    async def test_request_matcher_bool_rejected(self, bot):
        plugin = RecordingPlugin()

        async def handler(ctx: MatcherContext):
            return False  # 拒绝审批

        plugin.matchers = [self.make_event_matcher(handler, "request")]
        register_plugin(bot, plugin)

        event = self.make_request_event()
        ctx = bot.dispatcher.dispatch(event)
        reply, _ = await bot.dispatcher._run_event_matchers(bot, event, ctx)

        assert reply is False, f"reply 应保留 bool 类型 False，实际为 {type(reply)}: {reply!r}"

    async def test_notice_matcher_string_unchanged(self, bot):
        """notice 事件也应保留原始类型（但通常返回 str，确保不退化）"""
        plugin = RecordingPlugin()

        async def handler(ctx: MatcherContext):
            return "已处理"

        plugin.matchers = [self.make_event_matcher(handler, "notice")]
        register_plugin(bot, plugin)

        event = {"post_type": "notice", "notice_type": "notify", "user_id": 10001, "self_id": 20002}
        ctx = bot.dispatcher.dispatch(event)
        reply, _ = await bot.dispatcher._run_event_matchers(bot, event, ctx)

        assert reply == "已处理"
        assert isinstance(reply, str)
