"""消息分发器 - 事件路由与消息预处理"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

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
        self._handlers: dict[str, list] = {
            "message": [],       # 所有消息
            "message.group": [],  # 群消息
            "message.private": [], # 私聊消息
            "notice": [],         # 通知事件
            "request": [],        # 请求事件
            "meta_event": [],     # 元事件
        }

    def register(self, event_type: str, handler):
        """注册事件处理器"""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def on(self, event_type: str):
        """装饰器方式注册"""
        def decorator(func):
            self.register(event_type, func)
            return func
        return decorator

    async def dispatch(self, event: dict) -> Optional[MessageContext]:
        """分发事件"""
        post_type = event.get("post_type", "")

        # 构建消息上下文
        ctx = MessageContext(raw_event=event)
        if post_type == "message":
            ctx = self._parse_message(event)

        # 分发到对应处理器
        handlers = []
        handlers.extend(self._handlers.get(post_type, []))
        if post_type == "message":
            handlers.extend(self._handlers.get(f"message.{event.get('message_type', '')}", []))

        for handler in handlers:
            try:
                result = await handler(event, ctx)
                if result is not None:
                    return result
            except Exception:
                logger.exception(f"处理器异常: {getattr(handler, '__name__', repr(handler))}")

        return ctx

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
                target = int(data.get("qq", 0))
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