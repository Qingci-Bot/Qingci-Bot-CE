"""内置管理插件 - 权限控制、开关、黑名单（Matcher 新式 API 示例）

使用 NoneBot2 风格的 Matcher/Rule/Permission 系统：
- on_command("clear", permission=SUPERUSER): 命令匹配 + 权限控制
- handler 接收 MatcherContext，通过 ctx.bot/plugin/matcher 访问依赖
"""

import logging
from typing import Optional

from ..base import PluginBase
from ..matcher import MatcherContext, on_command
from ..permission import SUPERUSER
from ...core.dispatcher import MessageContext

logger = logging.getLogger("qingci-bot.plugin.admin")


class AdminPlugin(PluginBase):
    """管理命令插件（Matcher 新式 API）"""

    name = "admin"
    version = "2.0.0"
    author = "Qingci-Bot"
    description = "管理命令插件：开关、清除对话、状态查询（Matcher API）"

    async def on_load(self):
        logger.info("管理插件已加载")

        # 注册 Matcher（handler 为 self 的方法，可访问 self.config/self.llm 等）
        self.matchers.append(
            on_command("clear", permission=SUPERUSER, priority=1)(self._cmd_clear)
        )
        self.matchers.append(
            on_command("status", permission=SUPERUSER, priority=1)(self._cmd_status)
        )
        self.matchers.append(
            on_command(("blacklist", "黑名单"), permission=SUPERUSER, priority=1)(
                self._cmd_blacklist
            )
        )

    async def on_unload(self):
        logger.info("管理插件已卸载")

    # ============ 旧式兼容（空实现，走 Matcher 调度） ============

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        # 已通过 Matcher 注册命令，这里返回 None 让旧式调度跳过
        return None

    # ============ Matcher handlers ============

    async def _cmd_clear(self, ctx: MatcherContext) -> str:
        """清除对话历史"""
        if self.llm:
            self.llm.clear_session(
                message_type=ctx.message_type,
                group_id=ctx.group_id,
                user_id=ctx.user_id,
            )
        return "对话历史已清除。"

    async def _cmd_status(self, ctx: MatcherContext) -> str:
        """查看 Bot 状态"""
        connected = self.connection.is_connected if self.connection else False
        llm_ok = await self.llm.check_availability() if self.llm else False
        msg_count = await self.db.get_message_count() if self.db else 0
        return (
            f"Bot 状态:\n"
            f"  LLBot 连接: {'在线' if connected else '离线'}\n"
            f"  LLM 服务: {'可用' if llm_ok else '不可用'}\n"
            f"  消息记录: {msg_count} 条"
        )

    async def _cmd_blacklist(self, ctx: MatcherContext) -> str:
        """黑名单管理: /blacklist add/remove <qq>"""
        args = ctx.args.strip()
        if not args:
            return "格式: /blacklist add/remove <QQ号>"

        parts = args.split()
        if len(parts) < 2:
            return "格式: /blacklist add/remove <QQ号>"

        action = parts[0]
        try:
            target = int(parts[1])
        except ValueError:
            return "格式: /blacklist add/remove <QQ号>"

        cfg = self.config.bot
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
