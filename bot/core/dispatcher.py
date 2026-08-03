"""消息分发器 - 事件路由、消息预处理、Matcher 调度

双轨调度：
1. 新式 Matcher：按 priority 排序，检查 rule + permission，匹配则执行 handler
2. 旧式 on_message：Matcher 全部未匹配后，依次调用插件的 on_message

Matcher handler 返回非 None（回复文本）则停止整个分发链。
block=True 的 Matcher 匹配后（无论 handler 返回什么）停止后续 Matcher。
已注册 Matcher 的插件不再走旧式 on_message 调度。
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("qingci-bot.dispatcher")


@dataclass
class MessageContext:
    """解析后的消息上下文"""
    # 原始事件
    raw_event: dict

    # 基础信息
    post_type: str = ""
    message_type: str = ""          # group / private
    sub_type: str = ""             # normal / anonymous / notice
    message_id: str = ""
    user_id: int = 0
    group_id: int = 0
    self_id: int = 0               # Bot 自己的 QQ 号

    # 消息内容
    raw_message: str = ""           # CQ 码原始文本
    plain_text: str = ""            # 纯文本
    at_list: list[int] = field(default_factory=list)  # 被 @ 的用户列表
    is_at_bot: bool = False         # 是否 @ 了 Bot
    images: list[str] = field(default_factory=list)   # 图片 URL 列表

    # 回复专用
    sender: dict = field(default_factory=dict)         # 发送者信息


class MessageDispatcher:
    """消息分发器：解析事件、路由到插件"""

    def __init__(self):
        pass

    async def dispatch(self, event: dict) -> Optional[MessageContext]:
        """分发事件（仅解析，不执行 Matcher）

        Matcher 调度由 PluginManager + Dispatcher.run_matchers 完成。
        """
        post_type = event.get("post_type", "")

        # 构建消息上下文
        ctx = MessageContext(raw_event=event)
        if post_type == "message":
            ctx = self._parse_message(event)

        return ctx

    async def run_matchers(self, bot, event: dict, ctx: MessageContext) -> Optional[str]:
        """执行 Matcher 调度（消息事件）

        Returns:
            非 None: 匹配到的 handler 返回的回复文本
            None: 无 Matcher 匹配
        """
        from ..plugin.matcher import MatcherContext

        post_type = event.get("post_type", "")
        if post_type != "message":
            # notice/request 事件走 Matcher 调度（如果有注册）
            return await self._run_event_matchers(bot, event, ctx)

        # 收集所有 Matcher 并按 priority 排序
        matchers = bot.plugin_manager.all_matchers()
        if not matchers:
            return None

        sorted_matchers = sorted(matchers, key=lambda m: m.priority)

        for matcher in sorted_matchers:
            # 每次匹配用全新的 MatcherContext（避免 rule 修改污染后续）
            mctx = MatcherContext.from_message_context(ctx, bot=bot, plugin=None, matcher=matcher)

            # 找到所属插件
            if matcher.owner:
                plugin = bot.plugin_manager.get(matcher.owner)
                if plugin is None:
                    continue  # 插件已卸载
                mctx.plugin = plugin

            try:
                # 事件类型检查
                if matcher.event_type != "message":
                    continue

                # 权限检查
                if not await matcher.permission.check(bot, event, mctx):
                    continue

                # 规则检查（可能修改 mctx.command/args/match）
                if not await matcher.rule.check(bot, event, mctx):
                    continue

                # 匹配成功，执行 handler
                mctx.matcher = matcher
                result = matcher.handler(mctx)
                if hasattr(result, "__await__"):
                    result = await result

                # handler 返回非 None = 有回复，停止分发
                if result is not None:
                    return result

                # block=True 则停止后续 Matcher（即使 handler 返回 None）
                if matcher.block:
                    return None

            except Exception:
                logger.exception(
                    f"Matcher 执行异常: owner={matcher.owner}, "
                    f"handler={getattr(matcher.handler, '__name__', repr(matcher.handler))}"
                )
                if matcher.block:
                    return None

        return None

    async def _run_event_matchers(self, bot, event: dict, ctx: MessageContext) -> Optional[str]:
        """执行 notice/request 事件的 Matcher 调度"""
        from ..plugin.matcher import MatcherContext

        matchers = bot.plugin_manager.all_matchers()
        post_type = event.get("post_type", "")

        sorted_matchers = sorted(
            [m for m in matchers if m.event_type == post_type],
            key=lambda m: m.priority,
        )

        for matcher in sorted_matchers:
            mctx = MatcherContext.from_message_context(ctx, bot=bot, plugin=None, matcher=matcher)
            if matcher.owner:
                mctx.plugin = bot.plugin_manager.get(matcher.owner)
                if mctx.plugin is None:
                    continue

            try:
                if not await matcher.permission.check(bot, event, mctx):
                    continue
                if not await matcher.rule.check(bot, event, mctx):
                    continue

                mctx.matcher = matcher
                result = matcher.handler(mctx)
                if hasattr(result, "__await__"):
                    result = await result
                if result is not None:
                    return result
                if matcher.block:
                    return None
            except Exception:
                logger.exception(f"事件 Matcher 执行异常: owner={matcher.owner}")

        return None

    def _parse_message(self, event: dict) -> MessageContext:
        """解析消息内容，提取 CQ 码"""
        ctx = MessageContext(raw_event=event)

        ctx.post_type = event.get("post_type", "")
        ctx.message_type = event.get("message_type", "")
        ctx.sub_type = event.get("sub_type", "")
        ctx.message_id = str(event.get("message_id", ""))
        ctx.user_id = event.get("user_id", 0)
        ctx.group_id = event.get("group_id", 0)
        ctx.self_id = event.get("self_id", 0)
        ctx.sender = event.get("sender", {})
        ctx.raw_message = event.get("raw_message", "")

        # 解析 message 数组
        message = event.get("message", [])
        text_parts = []
        for seg in message:
            seg_type = seg.get("type", "")
            data = seg.get("data", {})
            if seg_type == "text":
                text_parts.append(data.get("text", ""))
            elif seg_type == "at":
                qq_val = data.get("qq", "0")
                # @全体成员 时 qq="all"，不解析为数字
                if qq_val == "all":
                    ctx.at_list.append(0)  # 0 表示全体成员
                else:
                    try:
                        target = int(qq_val)
                    except (ValueError, TypeError):
                        target = 0
                    ctx.at_list.append(target)
                    if target == ctx.self_id:
                        ctx.is_at_bot = True
            elif seg_type == "image":
                ctx.images.append(data.get("url", ""))

        ctx.plain_text = "".join(text_parts).strip()
        return ctx

    @staticmethod
    def build_cq_at(qq: int) -> str:
        """构建 CQ @ 码"""
        return f"[CQ:at,qq={qq}]"

    @staticmethod
    def build_cq_image(file: str) -> str:
        """构建 CQ 图片码"""
        return f"[CQ:image,file={file}]"

    @staticmethod
    def build_cq_reply(message_id: str) -> str:
        """构建 CQ 回复码"""
        return f"[CQ:reply,id={message_id}]"
