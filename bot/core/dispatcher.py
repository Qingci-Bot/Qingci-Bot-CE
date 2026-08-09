"""消息分发器 - 事件路由、消息预处理、Matcher 调度

双轨调度：
1. 新式 Matcher：按 priority 排序，检查 rule + permission，匹配则执行 handler
2. 旧式 on_message：Matcher 全部未匹配后，依次调用插件的 on_message

Matcher handler 返回非 None（回复文本）则停止整个分发链。
block=True 的 Matcher 匹配后（无论 handler 返回什么）停止后续 Matcher。
已注册 Matcher 的插件不再走旧式 on_message 调度。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .bot import QingciBot

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
    # 刻意保持 str 类型（OneBot 事件原始为 int）：
    # 支持 chat.py 的 f"{message_id}_reply" 复合标识与数据库 str 列的兼容，勿改为 int
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

    # 所有原始消息段
    segments: list[dict] = field(default_factory=list)  # 完整消息段列表


class MessageDispatcher:
    """消息分发器：解析事件、路由到插件"""

    def __init__(self):
        pass

    def dispatch(self, event: dict) -> MessageContext:
        """分发事件（仅解析，不执行 Matcher）

        Matcher 调度由 PluginManager + Dispatcher.run_matchers 完成。
        对 notice/request 等非消息事件也填充基础字段，使事件 Matcher 的
        权限/规则检查（如按 user_id/group_id 过滤）可用。
        """
        post_type = event.get("post_type", "")

        ctx = MessageContext(raw_event=event)
        if post_type == "message":
            ctx = self._parse_message(event)
        else:
            ctx.post_type = post_type
            ctx.sub_type = event.get("sub_type", "")
            ctx.self_id = self._safe_int(event.get("self_id"))
            ctx.user_id = self._safe_int(event.get("user_id"))
            ctx.group_id = self._safe_int(event.get("group_id"))
            ctx.sender = event.get("sender", {}) or {}

        return ctx

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        """安全地将值转为 int，非法值返回 default（避免异常吞掉整个事件）"""
        try:
            return int(value or 0)
        except (ValueError, TypeError):
            return default

    async def run_matchers(self, bot: "QingciBot", event: dict, ctx: MessageContext) -> tuple[Optional[str], bool]:
        """执行 Matcher 调度（消息事件）

        返回 (reply, blocked)：
        - reply: handler 返回的回复文本（None 表示无回复）
        - blocked: 是否发生 block 语义（匹配成功且 block=True，
          或已有回复），此时应停止整个分发链（含旧式回调）
        """
        from ..plugin.matcher import MatcherContext

        matchers = bot.plugin_manager.all_matchers()
        if not matchers:
            return None, False

        for matcher in matchers:
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

                # 一次性（temp）匹配器：执行后自动移除，避免重复响应
                if matcher.temp:
                    bot.plugin_manager.remove_temp_matcher(matcher)

                # handler 返回非 None = 有回复，停止分发
                if result is not None:
                    return result, True

                # block=True 则停止后续 Matcher 与旧式回调（即使 handler 返回 None）
                if matcher.block:
                    return None, True

            except Exception:
                logger.exception(
                    f"Matcher 执行异常: owner={matcher.owner}, "
                    f"handler={getattr(matcher.handler, '__name__', repr(matcher.handler))}"
                )
                # 异常不应触发 block 语义，继续执行后续 Matcher
                continue

        return None, False

    async def _run_event_matchers(self, bot: "QingciBot", event: dict, ctx: MessageContext) -> tuple[Optional[str], bool]:
        """执行 notice/request 事件的 Matcher 调度，返回 (reply, blocked)"""
        from ..plugin.matcher import MatcherContext

        matchers = bot.plugin_manager.all_matchers()
        post_type = event.get("post_type", "")

        event_matchers = [m for m in matchers if m.event_type == post_type]

        for matcher in event_matchers:
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
                # 一次性（temp）匹配器：执行后自动移除
                if matcher.temp:
                    bot.plugin_manager.remove_temp_matcher(matcher)
                if result is not None:
                    return result, True
                if matcher.block:
                    return None, True
            except Exception:
                logger.exception(f"事件 Matcher 执行异常: owner={matcher.owner}")
                # 异常不应触发 block 语义，继续执行后续 Matcher
                continue

        return None, False

    def _parse_message(self, event: dict) -> MessageContext:
        """解析消息内容，提取 CQ 码"""
        ctx = MessageContext(raw_event=event)

        ctx.post_type = event.get("post_type", "")
        ctx.message_type = event.get("message_type", "")
        ctx.sub_type = event.get("sub_type", "")
        # 消息缺失 message_id 时生成占位 id，避免空串在 messages 表的
        # unique 约束下被静默丢弃（第二条起全部冲突）
        raw_mid = event.get("message_id")
        if raw_mid not in (None, "", 0):
            ctx.message_id = str(raw_mid)
        else:
            ctx.message_id = f"gen-{event.get('user_id', 0)}-{int(time.time() * 1000)}"
        ctx.self_id = self._safe_int(event.get("self_id"))
        ctx.user_id = self._safe_int(event.get("user_id"))
        ctx.group_id = self._safe_int(event.get("group_id"))
        ctx.sender = event.get("sender", {}) or {}
        ctx.raw_message = event.get("raw_message", "")

        # 解析 message 数组
        message = event.get("message", [])
        if isinstance(message, str):
            message = [{"type": "text", "data": {"text": message}}]
        elif not isinstance(message, list):
            message = []
        text_parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            ctx.segments.append(seg)
            seg_type = seg.get("type", "")
            data = seg.get("data", {})
            if not isinstance(data, dict):
                data = {}
            if seg_type == "text":
                text_parts.append(str(data.get("text") or ""))
            elif seg_type == "at":
                qq_val = data.get("qq", "0")
                if qq_val == "all" or qq_val == 0 or qq_val == "0":
                    ctx.at_list.append(0)  # 0 表示全体成员
                else:
                    try:
                        target = int(qq_val)
                    except (ValueError, TypeError):
                        continue  # 跳过无效 @
                    if target == 0:
                        continue
                    ctx.at_list.append(target)
                    if ctx.self_id and target == ctx.self_id:
                        ctx.is_at_bot = True
            elif seg_type == "image":
                ctx.images.append(data.get("url") or "")

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
