"""内置管理插件 - 权限控制、开关、黑名单"""

import logging
from typing import Optional

from ..base import PluginBase
from ...core.dispatcher import MessageContext

logger = logging.getLogger("qingci-bot.plugin.admin")


class AdminPlugin(PluginBase):
    """管理命令插件"""

    name = "admin"
    version = "1.0.0"
    author = "Qingci-Bot"
    description = "管理命令插件：开关、清除对话、状态查询"

    async def on_load(self):
        logger.info("管理插件已加载")

    async def on_unload(self):
        logger.info("管理插件已卸载")

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        text = ctx.plain_text.strip()
        if not text:
            return None

        cfg = self.config.bot

        # 管理命令仅管理员可用
        if ctx.user_id not in cfg.admin_users:
            return None

        # /clear - 清除对话历史
        if text == "/clear":
            if self.llm:
                self.llm.clear_session(
                    message_type=ctx.message_type,
                    group_id=ctx.group_id,
                    user_id=ctx.user_id,
                )
            return "对话历史已清除。"

        # /status - 查看 Bot 状态
        if text == "/status":
            connected = self.connection.is_connected if self.connection else False
            llm_ok = await self.llm.check_availability() if self.llm else False
            msg_count = await self.db.get_message_count() if self.db else 0
            return (
                f"Bot 状态:\n"
                f"  LLBot 连接: {'在线' if connected else '离线'}\n"
                f"  LLM 服务: {'可用' if llm_ok else '不可用'}\n"
                f"  消息记录: {msg_count} 条"
            )

        # /blacklist add/remove <qq>
        if text == "/blacklist" or text.startswith("/blacklist "):
            parts = text.split()
            if len(parts) >= 3:
                action = parts[1]
                try:
                    target = int(parts[2])
                except ValueError:
                    return "格式: /blacklist add/remove <QQ号>"

                if action == "add":
                    if target not in cfg.user_blacklist:
                        cfg.user_blacklist.append(target)
                        self.config.save()
                        return f"已将 {target} 加入黑名单。"
                    return f"{target} 已在黑名单中。"
                elif action == "remove":
                    if target in cfg.user_blacklist:
                        cfg.user_blacklist.remove(target)
                        self.config.save()
                        return f"已将 {target} 移出黑名单。"
                    return f"{target} 不在黑名单中。"

            return "格式: /blacklist add/remove <QQ号>"

        return None