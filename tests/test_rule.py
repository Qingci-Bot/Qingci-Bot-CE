"""Rule 规则系统测试：内置规则与组合逻辑"""

import pytest

from bot.plugin.rule import (
    command,
    contains,
    endswith,
    fullmatch,
    is_group,
    is_private,
    keyword,
    regex,
    startswith,
    to_me,
)


def make_ctx(plain_text="", message_type="private", is_at_bot=False):
    """构造最小 MessageContext 兼容对象"""

    class Ctx:
        pass

    c = Ctx()
    c.plain_text = plain_text
    c.message_type = message_type
    c.is_at_bot = is_at_bot
    c.command = ""
    c.args = ""
    c.match = None
    return c


async def check(rule, ctx, bot=None):
    return await rule.check(bot, {}, ctx)


class TestBuiltinRules:
    async def test_startswith(self):
        r = startswith("翻译")
        ctx = make_ctx("翻译 hello")
        assert await check(r, ctx)
        assert ctx.args == "hello"

    async def test_startswith_tuple(self):
        r = startswith(("a", "b"))
        assert await check(r, make_ctx("abc"))
        assert await check(r, make_ctx("bcd"))

    async def test_endswith(self):
        r = endswith("!")
        assert await check(r, make_ctx("hello!"))

    async def test_fullmatch(self):
        r = fullmatch("ping")
        assert await check(r, make_ctx("ping"))
        assert not await check(r, make_ctx("ping!"))
        # tuple 别名
        assert await check(fullmatch(("ping", "pong")), make_ctx("pong"))

    async def test_contains(self):
        r = contains("天气")
        assert await check(r, make_ctx("今天天气不错"))

    async def test_regex(self):
        r = regex(r"(\d+)点(\d+)分")
        ctx = make_ctx("提醒我 10点30分")
        assert await check(r, ctx)
        assert ctx.match.group(1) == "10"
        assert ctx.match.group(2) == "30"

    async def test_command(self):
        r = command("ping")
        ctx = make_ctx("/ping")
        assert await check(r, ctx)
        assert ctx.command == "ping"
        assert ctx.args == ""

    async def test_command_args(self):
        r = command("greet")
        ctx = make_ctx("/greet 小明")
        assert await check(r, ctx)
        assert ctx.command == "greet"
        assert ctx.args == "小明"

    async def test_command_alias(self):
        r = command(("help", "帮助"))
        assert await check(r, make_ctx("/帮助"))
        assert await check(r, make_ctx("/help"))

    async def test_command_requires_space(self):
        r = command("ping")
        assert not await check(r, make_ctx("/ping123"))

    async def test_to_me(self):
        assert await check(to_me(), make_ctx("hi", is_at_bot=True))
        assert await check(to_me(), make_ctx("hi", message_type="private"))
        assert not await check(to_me(), make_ctx("hi", message_type="group", is_at_bot=False))

    async def test_is_private_and_group(self):
        assert await check(is_private(), make_ctx(message_type="private"))
        assert await check(is_group(), make_ctx(message_type="group"))

    async def test_keyword(self):
        r = keyword("提醒")
        assert await check(r, make_ctx("请提醒我"))

    async def test_keyword_empty_raises(self):
        with pytest.raises(ValueError):
            keyword()


class TestRuleComposition:
    async def test_and(self):
        r = is_group() & startswith("!")
        assert await check(r, make_ctx("!hi", message_type="group"))
        assert not await check(r, make_ctx("!hi", message_type="private"))

    async def test_or(self):
        r = is_private() | is_group()
        assert await check(r, make_ctx(message_type="private"))
        assert await check(r, make_ctx(message_type="group"))

    async def test_or_restores_ctx(self):
        # 左侧 command 规则写入 ctx.command 但失败后，右侧不应被污染
        left = command("ping")
        right = startswith("hello")
        r = left | right
        ctx = make_ctx("hello world")
        assert await check(r, ctx)
        assert ctx.command == ""  # 左侧失败已恢复

    async def test_invert(self):
        r = ~is_private()
        assert await check(r, make_ctx(message_type="group"))
        assert not await check(r, make_ctx(message_type="private"))

    async def test_chained(self):
        r = is_group() & (startswith("!") | startswith("#"))
        assert await check(r, make_ctx("!cmd", message_type="group"))
        assert await check(r, make_ctx("#cmd", message_type="group"))
        assert not await check(r, make_ctx("cmd", message_type="group"))
