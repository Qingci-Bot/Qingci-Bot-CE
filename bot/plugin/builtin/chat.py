"""内置聊天插件 - 对接 LLM 进行对话"""

import logging
from typing import Optional

from ..base import PluginBase
from ...core.dispatcher import MessageContext

logger = logging.getLogger("qingci-bot.plugin.chat")


class ChatPlugin(PluginBase):
    """LLM 对话插件"""

    name = "chat"
    version = "1.0.0"
    author = "Qingci-Bot"
    description = "LLM 智能对话插件"

    async def on_load(self):
        logger.info("聊天插件已加载")

    async def on_unload(self):
        logger.info("聊天插件已卸载")

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        if not ctx.plain_text:
            return None

        # 权限检查
        cfg = self.config.bot
        if ctx.user_id in cfg.user_blacklist:
            return None
        if ctx.group_id and ctx.group_id in cfg.group_blacklist:
            return None

        # 触发条件判断
        should_reply = False
        if cfg.trigger_mode == "always":
            should_reply = True
        elif cfg.trigger_mode == "at":
            should_reply = ctx.is_at_bot
        elif cfg.trigger_mode == "keyword":
            for kw in cfg.trigger_keywords:
                if ctx.plain_text.startswith(kw):
                    ctx.plain_text = ctx.plain_text[len(kw):].strip()
                    should_reply = True
                    break

        if not should_reply:
            return None

        # 调用 LLM
        reply = await self.llm.chat(
            message=ctx.plain_text,
            message_type=ctx.message_type,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
        )

        # 保存消息记录
        if self.db:
            group_id = ctx.group_id if ctx.message_type == "group" else None
            await self.db.save_message(
                message_id=ctx.message_id,
                user_id=ctx.user_id,
                group_id=group_id,
                content=ctx.plain_text,
                message_type=ctx.message_type,
                role="user",
            )
            await self.db.save_message(
                message_id=f"{ctx.message_id}_reply",
                user_id=ctx.self_id,
                group_id=group_id,
                content=reply,
                message_type=ctx.message_type,
                role="assistant",
            )
        # 实时广播（独立于数据库）
        from ...core.broadcast import broadcast_message
        from datetime import datetime
        now = datetime.now().isoformat()
        group_id = ctx.group_id if ctx.message_type == "group" else None
        await broadcast_message({
            "message_id": ctx.message_id,
            "user_id": ctx.user_id,
            "group_id": group_id,
            "content": ctx.plain_text,
            "message_type": ctx.message_type,
            "role": "user",
            "created_at": now,
        })
        await broadcast_message({
            "message_id": f"{ctx.message_id}_reply",
            "user_id": ctx.self_id,
            "group_id": group_id,
            "content": reply,
            "message_type": ctx.message_type,
            "role": "assistant",
            "created_at": now,
        })

        return reply