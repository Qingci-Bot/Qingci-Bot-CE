"""规则系统 - 借鉴 NoneBot2 的 Rule 设计

支持规则组合（AND/OR/NOT），内置常用规则：
- startswith(prefix): 命令前缀
- endswith(suffix): 命令后缀
- fullmatch(text): 完全匹配
- contains(text): 包含文本
- regex(pattern): 正则匹配
- command(cmd): 命令匹配（自动解析参数，支持别名）
- to_me(): @ 机器人
- is_private() / is_group(): 会话类型
- keyword(*kws): 关键词触发

规则检查基于 event + ctx，返回 bool。
匹配后可修改 ctx（如 command 规则解析参数写入 ctx.command/ctx.args）。
"""

import re
from typing import Callable, Union

from ..core.dispatcher import MessageContext


class Rule:
    """规则对象，支持 & | ~ 组合"""

    def __init__(self, checker: Callable = None):
        # checker: async (bot, event, ctx) -> bool
        self._checkers: list[Callable] = []
        if checker is not None:
            self._checkers.append(checker)

    async def check(self, bot, event: dict, ctx: MessageContext) -> bool:
        """检查规则：所有 checker 都通过才返回 True（AND 逻辑）

        checker 可修改 ctx（如 command 规则写入解析结果）。
        """
        for checker in self._checkers:
            try:
                result = checker(bot, event, ctx)
                if hasattr(result, "__await__"):
                    result = await result
                if not result:
                    return False
            except Exception:
                return False
        return True

    def __and__(self, other: "Rule") -> "Rule":
        """AND 组合"""
        rule = Rule()
        rule._checkers = self._checkers + other._checkers
        return rule

    def __or__(self, other: "Rule") -> "Rule":
        """OR 组合：满足其一即可"""
        rule = Rule()
        left_checkers = self._checkers[:]
        right_checkers = other._checkers[:]
        async def _or_checker(bot, event, ctx):
            # 尝试左侧（可能修改 ctx，用副本避免污染）
            left_ok = True
            for c in left_checkers:
                r = c(bot, event, ctx)
                if hasattr(r, "__await__"):
                    r = await r
                if not r:
                    left_ok = False
                    break
            if left_ok:
                return True
            # 左侧失败，尝试右侧
            for c in right_checkers:
                r = c(bot, event, ctx)
                if hasattr(r, "__await__"):
                    r = await r
                if not r:
                    return False
            return True
        rule._checkers = [_or_checker]
        return rule

    def __invert__(self) -> "Rule":
        """NOT 组合：取反"""
        rule = Rule()
        original_checkers = self._checkers[:]
        async def _not_checker(bot, event, ctx):
            for c in original_checkers:
                r = c(bot, event, ctx)
                if hasattr(r, "__await__"):
                    r = await r
                if not r:
                    return True
            return False
        rule._checkers = [_not_checker]
        return rule


# ============ 内置规则工厂 ============

def startswith(prefix: Union[str, tuple[str, ...]]) -> Rule:
    """前缀匹配

    匹配后自动从 plain_text 中去除前缀，写入 ctx.args。
    """
    prefixes = (prefix,) if isinstance(prefix, str) else tuple(prefix)

    def _check(bot, event, ctx):
        text = ctx.plain_text
        for p in prefixes:
            if text.startswith(p):
                ctx.args = text[len(p):].strip()
                return True
        return False

    return Rule(_check)


def endswith(suffix: Union[str, tuple[str, ...]]) -> Rule:
    """后缀匹配"""
    suffixes = (suffix,) if isinstance(suffix, str) else tuple(suffix)
    return Rule(lambda bot, event, ctx: any(ctx.plain_text.endswith(s) for s in suffixes))


def fullmatch(text: Union[str, tuple[str, ...]]) -> Rule:
    """完全匹配"""
    texts = (text,) if isinstance(text, str) else tuple(text)
    return Rule(lambda bot, event, ctx: ctx.plain_text in texts)


def contains(keyword: str) -> Rule:
    """包含文本"""
    return Rule(lambda bot, event, ctx: keyword in ctx.plain_text)


def regex(pattern: Union[str, re.Pattern], flags: int = 0) -> Rule:
    """正则匹配

    匹配后将 Match 对象存入 ctx.match。
    """
    compiled = re.compile(pattern, flags) if isinstance(pattern, str) else pattern

    def _check(bot, event, ctx):
        m = compiled.search(ctx.plain_text)
        if m:
            ctx.match = m
            return True
        return False

    return Rule(_check)


def command(cmd: Union[str, tuple[str, ...]]) -> Rule:
    """命令匹配

    支持别名（传入 tuple）。命令前缀自动处理（/ 或无前缀均可）。
    匹配后：
    - ctx.command: 匹配的命令名
    - ctx.args: 命令参数（去除命令后的文本，已 strip）
    """
    commands = (cmd,) if isinstance(cmd, str) else tuple(cmd)

    def _check(bot, event, ctx):
        text = ctx.plain_text
        # 支持 /cmd 和 cmd 两种形式
        if text.startswith("/"):
            text_for_match = text[1:]
        else:
            text_for_match = text

        for c in commands:
            # 命令后必须紧跟空格或为文本结尾
            if text_for_match == c:
                ctx.command = c
                ctx.args = ""
                return True
            if text_for_match.startswith(c + " "):
                ctx.command = c
                ctx.args = text_for_match[len(c):].strip()
                return True
            # 支持命令直接连参数无空格的情况（如 /ping123 不匹配 ping）
        return False

    return Rule(_check)


def to_me() -> Rule:
    """@ 机器人或私聊"""
    def _check(bot, event, ctx):
        return ctx.is_at_bot or ctx.message_type == "private"
    return Rule(_check)


def is_private() -> Rule:
    """私聊消息"""
    return Rule(lambda bot, event, ctx: ctx.message_type == "private")


def is_group() -> Rule:
    """群聊消息"""
    return Rule(lambda bot, event, ctx: ctx.message_type == "group")


def keyword(*keywords: str) -> Rule:
    """关键词触发（包含任一关键词）"""
    kws = keywords
    return Rule(lambda bot, event, ctx: any(k in ctx.plain_text for k in kws))
