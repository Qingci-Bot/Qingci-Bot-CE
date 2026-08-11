"""内置帮助插件 - 列出当前用户可用的命令

遍历 PluginManager 注册的所有 Matcher，按插件分组汇聚 /help 输出。
从 Matcher 的 meta 元信息（meta["command"] / meta["description"]）读取命令信息，
并逐个用 matcher.permission 过滤出当前用户可见的命令后格式化输出。
"""

import logging

from ..base import PluginBase, PluginStatus
from ..matcher import MatcherContext, on_command

logger = logging.getLogger("qingci-bot.plugin.help")


class HelpPlugin(PluginBase):
    """帮助命令插件（Matcher 新式 API）"""

    name = "help"
    version = "1.0.0"
    author = "Qingci-Bot CE"
    description = "显示可用命令列表"
    category = "tool"

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
        """列出当前用户有权限使用的命令，按插件分组，支持分类筛选"""
        event = ctx.raw_event or {}
        # 构建插件 → 命令列表（保留顺序）
        plugin_commands: dict[str, dict] = {}
        seen: set[str] = set()

        for matcher in self.bot.plugin_manager.all_matchers():
            cmd = matcher.meta.get("command") if matcher.meta else None
            if not cmd or cmd in seen:
                continue
            # 权限过滤
            try:
                visible = await matcher.permission.check(self.bot, event, ctx)
            except Exception:
                logger.warning(f"帮助权限检查异常: command={cmd}", exc_info=True)
                visible = False
            if not visible:
                continue
            seen.add(cmd)

            plugin = self.bot.plugin_manager.get(matcher.owner or "__unknown__")
            plugin_name = plugin.name if plugin else (matcher.owner or "未分类")
            plugin_category = getattr(plugin, "category", "") if plugin else ""
            plugin_desc = getattr(plugin, "description", "") if plugin else ""

            if plugin_name not in plugin_commands:
                plugin_commands[plugin_name] = {
                    "category": plugin_category,
                    "description": plugin_desc,
                    "commands": [],
                }
            desc = matcher.meta.get("description") or ""
            plugin_commands[plugin_name]["commands"].append((cmd, desc))

        if not plugin_commands:
            return "当前没有可用的命令。"

        # 按分类排序插件
        sorted_plugins = sorted(
            plugin_commands.items(),
            key=lambda item: (item[1]["category"] or "zzz", item[0]),
        )

        lines: list[str] = ["可用命令:"]
        current_category: str | None = None

        for plugin_name, info in sorted_plugins:
            category = info["category"] or "未分类"
            # 分类标题
            if category != current_category:
                current_category = category
                lines.append(f"\n━━━ {current_category} ━━━")

            cmds = info["commands"]
            if not cmds:
                continue

            lines.append(f"  [{plugin_name}]")
            for cmd, desc in cmds:
                lines.append(f"    /{cmd} - {desc}" if desc else f"    /{cmd}")

        return "\n".join(lines)
