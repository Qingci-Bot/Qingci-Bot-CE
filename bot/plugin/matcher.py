"""匹配器系统 - 借鉴 NoneBot2 的 Matcher 设计

核心概念：
- Matcher: 绑定 handler + rule + permission + priority 的匹配单元
- MatcherContext: 增强版 MessageContext，注入 bot/plugin/matcher 引用 + 命令解析结果
- 工厂函数: on_message / on_command / on_startswith / on_keyword / on_notice / on_request

注册方式（两种，都支持）：
1. 插件内注册（推荐，可访问 self）:
    async def on_load(self):
        self.matchers.append(
            on_command("ping")(self._handle_ping)
        )
2. 模块级装饰器:
    @on_command("ping")
    async def ping_handler(ctx: MatcherContext) -> str:
        return "pong"
    # PluginManager 加载时自动收集模块级 _matchers

handler 签名: async (ctx: MatcherContext) -> Optional[str]
返回非 None 则作为回复发送，并停止后续匹配器（除非 block=False）。
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING, Union

from ..core.dispatcher import MessageContext
from .permission import EVERYONE, Permission
from .rule import Rule

if TYPE_CHECKING:
    from ..core.bot import QingciBot
    from .base import PluginBase

logger = logging.getLogger("qingci-bot.matcher")


@dataclass
class MatcherContext(MessageContext):
    """匹配器上下文 - 增强版 MessageContext

    新增字段：
    - bot: Bot 实例引用（供模块级 handler 访问依赖）
    - plugin: 当前插件实例引用
    - matcher: 当前匹配器实例
    - command: 匹配到的命令名（command 规则写入）
    - args: 命令参数 / 前缀后的剩余文本（startswith/command 规则写入）
    - match: 正则 Match 对象（regex 规则写入）
    """
    bot: Optional["QingciBot"] = None
    plugin: Optional["PluginBase"] = None
    matcher: Optional["Matcher"] = None
    command: str = ""
    args: str = ""
    match: Optional[object] = None

    @classmethod
    def from_message_context(
        cls,
        ctx: MessageContext,
        bot: Optional["QingciBot"] = None,
        plugin: Optional["PluginBase"] = None,
        matcher: Optional["Matcher"] = None,
    ) -> "MatcherContext":
        """从 MessageContext 升级为 MatcherContext"""
        return cls(
            raw_event=ctx.raw_event,
            post_type=ctx.post_type,
            message_type=ctx.message_type,
            sub_type=ctx.sub_type,
            message_id=ctx.message_id,
            user_id=ctx.user_id,
            group_id=ctx.group_id,
            self_id=ctx.self_id,
            raw_message=ctx.raw_message,
            plain_text=ctx.plain_text,
            at_list=ctx.at_list,
            is_at_bot=ctx.is_at_bot,
            images=ctx.images,
            sender=ctx.sender,
            bot=bot,
            plugin=plugin,
            matcher=matcher,
        )


@dataclass
class Matcher:
    """事件匹配器

    Attributes:
        handler: 处理函数 async (ctx: MatcherContext) -> Optional[str]
        rule: 匹配规则
        permission: 权限要求
        priority: 优先级（越小越先执行，默认 1）
        block: 是否阻塞后续匹配器（默认 True）
        temp: 临时匹配器（执行一次后自动移除）
        owner: 所属插件名
        event_type: 事件类型（message/notice/request/meta_event）
    """
    handler: Callable
    rule: Rule = field(default_factory=Rule)
    permission: Permission = field(default_factory=lambda: EVERYONE)
    priority: int = 1
    block: bool = True
    temp: bool = False  # 临时匹配器（预留，当前未实现自动移除）
    owner: str = ""
    event_type: str = "message"


# ============ 工厂函数 ============

def _create_matcher(
    handler: Callable,
    rule: Rule,
    permission: Permission,
    priority: int,
    block: bool,
    temp: bool,
    event_type: str = "message",
) -> Matcher:
    return Matcher(
        handler=handler,
        rule=rule,
        permission=permission,
        priority=priority,
        block=block,
        temp=temp,
        event_type=event_type,
    )


def on_message(
    rule: Rule = None,
    permission: Permission = None,
    priority: int = 1,
    block: bool = True,
) -> Callable:
    """注册消息匹配器（装饰器工厂）

    用法:
        @on_message(rule=command("ping"), permission=SUPERUSER)
        async def handler(ctx: MatcherContext) -> str:
            return "pong"
    """
    def decorator(func: Callable) -> Matcher:
        m = _create_matcher(
            handler=func,
            rule=rule or Rule(),
            permission=permission or EVERYONE,
            priority=priority,
            block=block,
            temp=False,
            event_type="message",
        )
        _collect_module_matcher(m)
        return m
    return decorator


def on_command(
    cmd: Union[str, tuple[str, ...]],
    rule: Rule = None,
    permission: Permission = None,
    priority: int = 1,
    block: bool = True,
) -> Callable:
    """注册命令匹配器

    用法:
        @on_command("ping", permission=SUPERUSER)
        async def ping(ctx: MatcherContext) -> str:
            return "pong"

        # 支持别名
        @on_command(("help", "帮助"))
        async def help_cmd(ctx: MatcherContext) -> str:
            return "可用命令: ..."
    """
    from .rule import command as _command
    combined_rule = _command(cmd)
    if rule:
        combined_rule = combined_rule & rule

    def decorator(func: Callable) -> Matcher:
        m = _create_matcher(
            handler=func,
            rule=combined_rule,
            permission=permission or EVERYONE,
            priority=priority,
            block=block,
            temp=False,
            event_type="message",
        )
        _collect_module_matcher(m)
        return m
    return decorator


def on_startswith(
    prefix: Union[str, tuple[str, ...]],
    rule: Rule = None,
    permission: Permission = None,
    priority: int = 1,
    block: bool = True,
) -> Callable:
    """注册前缀匹配器"""
    from .rule import startswith as _startswith
    combined_rule = _startswith(prefix)
    if rule:
        combined_rule = combined_rule & rule

    def decorator(func: Callable) -> Matcher:
        m = _create_matcher(
            handler=func,
            rule=combined_rule,
            permission=permission or EVERYONE,
            priority=priority,
            block=block,
            temp=False,
            event_type="message",
        )
        _collect_module_matcher(m)
        return m
    return decorator


def on_keyword(
    keywords: Union[str, tuple[str, ...]],
    rule: Rule = None,
    permission: Permission = None,
    priority: int = 1,
    block: bool = True,
) -> Callable:
    """注册关键词匹配器"""
    from .rule import keyword as _keyword
    kws = (keywords,) if isinstance(keywords, str) else keywords
    combined_rule = _keyword(*kws)
    if rule:
        combined_rule = combined_rule & rule

    def decorator(func: Callable) -> Matcher:
        m = _create_matcher(
            handler=func,
            rule=combined_rule,
            permission=permission or EVERYONE,
            priority=priority,
            block=block,
            temp=False,
            event_type="message",
        )
        _collect_module_matcher(m)
        return m
    return decorator


def on_notice(
    rule: Rule = None,
    priority: int = 1,
    block: bool = True,
) -> Callable:
    """注册通知事件匹配器"""
    def decorator(func: Callable) -> Matcher:
        m = _create_matcher(
            handler=func,
            rule=rule or Rule(),
            permission=EVERYONE,
            priority=priority,
            block=block,
            temp=False,
            event_type="notice",
        )
        _collect_module_matcher(m)
        return m
    return decorator


def on_request(
    rule: Rule = None,
    priority: int = 1,
    block: bool = True,
) -> Callable:
    """注册请求事件匹配器"""
    def decorator(func: Callable) -> Matcher:
        m = _create_matcher(
            handler=func,
            rule=rule or Rule(),
            permission=EVERYONE,
            priority=priority,
            block=block,
            temp=False,
            event_type="request",
        )
        _collect_module_matcher(m)
        return m
    return decorator


# ============ 模块级 Matcher 收集 ============

# 模块级 Matcher 收集栈：插件加载时设置，收集到的 matcher 关联到当前插件
_matcher_collector: Optional[list] = None


def _collect_module_matcher(matcher: Matcher):
    """收集模块级注册的 Matcher

    两种情况：
    1. PluginManager 加载模块时设置了 _matcher_collector -> 收集到列表
    2. 插件在 on_load 中手动 self.matchers.append() -> 不收集（已是 Matcher 对象）
    """
    if _matcher_collector is not None:
        _matcher_collector.append(matcher)
    # 否则：matcher 直接作为装饰器返回值，由调用方自行处理


def begin_module_collection() -> list:
    """开始收集模块级 Matcher，返回收集列表"""
    global _matcher_collector
    _matcher_collector = []
    return _matcher_collector


def end_module_collection():
    """结束收集"""
    global _matcher_collector
    _matcher_collector = None
