"""内置聊天插件 - 对接 LLM 进行对话（Matcher 新式 API）

触发模式（运行时读 config，配置热更新无需重新注册 Matcher）：
- always: 所有消息触发
- at: @bot 触发（私聊默认触发）
- keyword: 前缀关键词触发，自动去除前缀

priority=50（低优先级，让管理命令 priority=1 先执行）
block=False（即使匹配也不阻止后续 Matcher，但返回回复会停止分发链）
"""

import logging
from datetime import datetime
from typing import Optional

from ..base import PluginBase
from ..matcher import MatcherContext, on_message
from ..permission import Permission
from ..rule import Rule

logger = logging.getLogger("qingci-bot.plugin.chat")


def chat_trigger() -> Rule:
    """聊天触发规则（运行时读 config.bot.trigger_mode）

    匹配后将实际消息文本写入 ctx.args：
    - always/at: ctx.args = ctx.plain_text
    - keyword: ctx.args = 去除前缀后的文本
    """

    async def _check(bot, event, ctx) -> bool:
        if not ctx.plain_text:
            return False

        cfg = bot.config.bot
        mode = cfg.trigger_mode

        if mode == "always":
            ctx.args = ctx.plain_text
            return True

        if mode == "at":
            # @bot 或私聊均触发
            if ctx.is_at_bot or ctx.message_type == "private":
                ctx.args = ctx.plain_text
                return True
            return False

        if mode == "keyword":
            for kw in cfg.trigger_keywords:
                if ctx.plain_text.startswith(kw):
                    ctx.args = ctx.plain_text[len(kw):].strip()
                    return True
            return False

        return False

    return Rule(_check)


def chat_permission() -> Permission:
    """聊天权限：非黑名单用户"""

    async def _check(bot, event, ctx) -> bool:
        cfg = bot.config.bot
        if ctx.user_id in cfg.user_blacklist:
            return False
        if ctx.group_id and ctx.group_id in cfg.group_blacklist:
            return False
        return True

    return Permission(_check)


class ChatPlugin(PluginBase):
    """LLM 对话插件（Matcher 新式 API）"""

    name = "chat"
    version = "2.0.0"
    author = "Qingci-Bot"
    description = "LLM 智能对话插件"

    async def on_load(self):
        logger.info("聊天插件已加载")
        self.matchers.append(
            on_message(
                rule=chat_trigger(),
                permission=chat_permission(),
                priority=50,   # 低优先级，让管理命令先执行
                block=False,   # 不阻止后续 Matcher（返回回复时 Dispatcher 自动停止）
            )(self._handle_chat)
        )

    async def on_unload(self):
        logger.info("聊天插件已卸载")

    async def _handle_chat(self, ctx: MatcherContext) -> Optional[str]:
        """处理聊天消息：调用 LLM + 保存记录 + 实时广播"""
        message = ctx.args
        if not message:
            return None

        # 调用 LLM
        reply = await self.llm.chat(
            message=message,
            message_type=ctx.message_type,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
        )
        if not reply:
            # LLM 调用失败（返回 None 或空字符串），不保存到 DB/广播
            return "抱歉，AI 服务暂时不可用，请稍后再试。"

        # 保存消息记录到数据库
        group_id = ctx.group_id if ctx.message_type == "group" else None
        if self.db:
            try:
                await self.db.save_message(
                    message_id=ctx.message_id,
                    user_id=ctx.user_id,
                    group_id=group_id,
                    content=message,
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
            except Exception:
                logger.exception("保存消息记录失败")

        # 实时广播（独立于数据库）
        try:
            from ...core.broadcast import broadcast_message
            now = datetime.now().isoformat()
            await broadcast_message({
                "message_id": ctx.message_id,
                "user_id": ctx.user_id,
                "group_id": group_id,
                "content": message,
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
        except Exception:
            logger.exception("广播消息失败")

        return reply
