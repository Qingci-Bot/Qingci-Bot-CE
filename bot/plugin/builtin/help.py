"""内置帮助插件 - 列出当前用户可用的命令

遍历 PluginManager 注册的所有 Matcher，读取批次 0 引入的 meta 元信息
（meta["command"] / meta["description"]），并逐个用 matcher.permission
过滤出当前用户可见的命令后格式化输出。
"""

import logging

from ..base import PluginBase
from ..matcher import MatcherContext, on_command

logger = logging.getLogger("qingci-bot.plugin.help")


class HelpPlugin(PluginBase):
    """帮助命令插件（Matcher 新式 API）"""

    name = "help"
    version = "1.0.0"
    author = "Qingci-Bot"
    description = "显示可用命令列表"

    async def on_load(self):
        logger.info("帮助插件已加载")
        self.matchers.append(
            on_command(
                ("help", "帮助"),
                description="显示可用命令",
                priority=1,
            )(self._cmd_help)
        )

    async def on_unload(self):
        logger.info("帮助插件已卸载")

    async def _cmd_help(self, ctx: MatcherContext) -> str:
        """列出当前用户有权限使用的命令"""
        lines: list[str] = []
        seen: set[str] = set()
        event = ctx.raw_event or {}

        for matcher in self.bot.plugin_manager.all_matchers():
            # 仅列出命令类 Matcher（on_command 注册时回填了 meta["command"]）
            cmd = matcher.meta.get("command") if matcher.meta else None
            if not cmd or cmd in seen:
                continue
            # 按各 Matcher 自身的权限过滤当前用户可见性
            try:
                visible = await matcher.permission.check(self.bot, event, ctx)
            except Exception:
                logger.warning(f"帮助权限检查异常: command={cmd}", exc_info=True)
                visible = False
            if not visible:
                continue
            seen.add(cmd)
            desc = matcher.meta.get("description") or ""
            lines.append(f"/{cmd} - {desc}" if desc else f"/{cmd}")

        if not lines:
            return "当前没有可用的命令。"
        return "可用命令:\n" + "\n".join(lines)
