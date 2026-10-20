"""内置帮助插件 - 列出当前用户可用的命令（渲染成图片，带文本兜底）

遍历 PluginManager 注册的所有 Matcher，按插件分组汇聚 /help 输出。
从 Matcher 的 meta 元信息（meta["command"] / meta["description"] /
meta["aliases"] / meta["hidden_in_help"]）读取命令信息，并逐个用
matcher.permission 过滤出当前用户可见的命令后格式化输出。

输出策略（help 长文本问题最终方案 F，见《help插件长文本问题评估报告》）：
- 首选：HTML → PNG 图片（依赖 bot.html_renderer）。图片不受 QQ/TG
  单条消息字数硬上限（2000/4096）约束，命令再多也不被"腰斩"；
  按插件拆图，单图高度超过预算时拆成多张。
- 兜底：纯文本（渲染能力不可用/渲染失败时）。行数超过阈值时折叠为
  仅命令名（方案 C），保证不开天窗。
- /help <插件名|分类|命令>：按目标筛选（方案 A），既是缩小图片内容，
  也提供可复制的文字详情，弥补"图片内命令名不可复制"的短板。

注意（P3 澄清）：help 自身 priority=1（高优先级），若其他插件注册同名
命令（如自定义 /help），本插件会优先响应——需要自定义 help 的插件请改用
更高 priority 或另起命令名。
"""

import asyncio
import logging
from html import escape
from typing import Any

from ...base import PluginBase
from ...matcher import MatcherContext, on_command

logger = logging.getLogger("qingci-bot.plugin.help")

# 渲染图片宽度 / 单图最大高度（超出按插件拆图，防内容被裁剪）
_IMAGE_WIDTH = 720
_IMAGE_MAX_HEIGHT = 4000
# 文本兜底：估算行数超过该阈值时折叠为仅命令名（不带描述/别名）
_TEXT_FOLD_LIMIT = 40
# 渲染临时文件延迟清理秒数（消息发送完成后清理，防磁盘残留）
_TMP_RETENTION = 300
# 渲染单次超时秒数
_RENDER_TIMEOUT = 30.0
# 高度估算单行余量（px）：行高略大于字号，避免内容被裁剪
_ROW_HEIGHT = 34
_GROUP_HEADER = 60
_GROUP_FOOTER = 40


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

    async def _cmd_help(self, ctx: MatcherContext) -> str | list[dict]:
        """列出当前用户有权限使用的命令，按插件分组；`/help <插件|分类|命令>` 筛选"""
        assert self.bot is not None  # 运行时由 on_bot_connect 注入，仅类型收缩
        target = (ctx.args or "").strip().lower()
        plugin_commands = await self._collect(ctx, target)

        if plugin_commands is None:
            hint = f"与「{target}」相关的" if target else ""
            return f"未找到{hint}命令。回复 /help 查看全部。"

        image = await self._try_render_help_image(plugin_commands)
        if image is not None:
            return image
        return self._build_help_text(plugin_commands)

    # ---- 收集 ----

    async def _collect(self, ctx: MatcherContext, target: str) -> dict[str, dict] | None:
        """收集当前用户可见命令（含 hidden_in_help 过滤与 target 筛选）

        返回 插件名 → {category, description, commands: [(cmd, aliases, desc)]}；
        无可见命令/无命中时返回 None。
        """
        assert self.bot is not None
        event = ctx.raw_event or {}
        plugin_commands: dict[str, dict] = {}
        seen: set[str] = set()

        for matcher in self.bot.plugin_manager.all_matchers():
            meta = matcher.meta or {}
            cmd = meta.get("command")
            if not cmd or cmd in seen:
                continue
            if meta.get("hidden_in_help"):
                # 内部/调试命令（on_command hidden_in_help=True）不进帮助列表
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
            # 筛选：按目标（插件名 / 分类 / 命令名）匹配
            if target and target not in (
                plugin_name.lower(),
                plugin_category.lower(),
                cmd.lower(),
            ):
                continue

            if plugin_name not in plugin_commands:
                plugin_commands[plugin_name] = {
                    "category": plugin_category,
                    "description": plugin_desc,
                    "commands": [],
                }
            desc = meta.get("description") or ""
            aliases = [str(a) for a in (meta.get("aliases") or [])]
            plugin_commands[plugin_name]["commands"].append((cmd, aliases, desc))

        return plugin_commands or None

    @staticmethod
    def _sorted_plugins(plugin_commands: dict[str, dict]) -> list[tuple[str, dict]]:
        """按分类（未分类垫底）与插件名排序"""
        return sorted(
            plugin_commands.items(),
            key=lambda item: (item[1]["category"] or "zzz", item[0]),
        )

    # ---- 渲染成图片（方案 F） ----

    async def _try_render_help_image(self, plugin_commands: dict[str, dict]) -> list[dict] | None:
        """尝试把命令列表渲染为 PNG 图片；能力不可用/失败返回 None（上层回退文本）

        按插件拆图：估算总高超过单图预算时拆成多张；单插件命令过多时
        该插件内部再拆子图。渲染产物为临时文件，安排延迟清理。
        """
        renderer = getattr(self.bot, "html_renderer", None) if self.bot else None
        if renderer is None or not getattr(renderer, "is_supported", lambda: False)():
            return None
        chunks = self._chunk_plugins(self._sorted_plugins(plugin_commands))

        segments: list[dict] = []
        try:
            for chunk in chunks:
                html = self._build_help_html(chunk)
                height = self._estimate_chunk_height(chunk)
                path = await renderer.render_html(
                    html,
                    width=_IMAGE_WIDTH,
                    height=height,
                    image_format="png",
                    timeout=_RENDER_TIMEOUT,
                )
                segments.append({"type": "image", "data": {"file": str(path)}})
                self._schedule_cleanup(path)
        except Exception:
            logger.debug("帮助命令渲染失败，回退文本", exc_info=True)
            return None
        return segments or None

    @classmethod
    def _chunk_plugins(cls, sorted_plugins: list[tuple[str, dict]]) -> list[list[tuple[str, dict]]]:
        """按高度预算分批；单插件超预算时内部拆分为多份（同插件名）"""
        chunks: list[list[tuple[str, dict]]] = []
        current: list[tuple[str, dict]] = []
        current_h = 0

        def _add(group: tuple[str, dict]) -> None:
            nonlocal current, current_h
            h = cls._estimate_group_height(group)
            if current and current_h + h > _IMAGE_MAX_HEIGHT:
                chunks.append(current)
                current, current_h = [], 0
            current.append(group)
            current_h += h

        for group in sorted_plugins:
            if cls._estimate_group_height(group) > _IMAGE_MAX_HEIGHT:
                for piece in cls._split_oversize_group(group):
                    _add(piece)
                continue
            _add(group)
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _split_oversize_group(group: tuple[str, dict]) -> list[tuple[str, dict]]:
        """单插件命令过多时拆分：每份预算内，复用同一插件名"""
        name, info = group
        rows = info["commands"]
        per = max(1, (_IMAGE_MAX_HEIGHT - _GROUP_HEADER - _GROUP_FOOTER) // _ROW_HEIGHT)
        pieces: list[tuple[str, dict]] = []
        for i in range(0, len(rows), per):
            sub = dict(info)
            sub["commands"] = rows[i : i + per]
            pieces.append((name, sub))
        return pieces

    @staticmethod
    def _estimate_group_height(group: tuple[str, dict]) -> int:
        """单插件组估算高度：标题 + 每行命令 + 底部留白"""
        rows = len(group[1]["commands"])
        return _GROUP_HEADER + rows * _ROW_HEIGHT + _GROUP_FOOTER

    @staticmethod
    def _estimate_chunk_height(chunk: list[tuple[str, dict]]) -> int:
        total = sum(HelpPlugin._estimate_group_height(g) for g in chunk)
        return max(200, min(total, _IMAGE_MAX_HEIGHT))

    @staticmethod
    def _build_help_html(chunk: list[tuple[str, dict]]) -> str:
        """构建 help 卡片 HTML（内联 CSS；命令名/描述/别名一律 escape 防注入）"""
        cards: list[str] = []
        for plugin_name, info in chunk:
            rows: list[str] = []
            for cmd, aliases, desc in info["commands"]:
                cells = [f'<span class="cmd">/{escape(cmd)}</span>']
                if aliases:
                    aliases_text = " / ".join(escape(a) for a in aliases)
                    cells.append(f'<span class="aliases">({aliases_text})</span>')
                if desc:
                    cells.append(f'<span class="desc">{escape(desc)}</span>')
                rows.append(f'<div class="row">{"".join(cells)}</div>')
            cards.append(
                f'<div class="plugin"><div class="pname">{escape(plugin_name)}</div>'
                f"{''.join(rows)}</div>"
            )
        return (
            '<html><head><meta charset="utf-8"><style>'
            "body{margin:0;padding:24px;background:#1e1f2e;color:#e8e9f2;"
            'font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;}'
            ".plugin{background:#2a2b3d;border-radius:12px;padding:16px 20px;margin-bottom:16px;}"
            ".pname{font-size:20px;font-weight:700;color:#ffd700;margin-bottom:12px;}"
            ".row{display:flex;align-items:baseline;gap:12px;padding:6px 0;"
            "border-bottom:1px solid rgba(255,255,255,.06);font-size:15px;}"
            ".cmd{color:#7ec8ff;font-weight:600;white-space:nowrap;}"
            ".aliases{color:#9aa0b8;font-size:13px;}"
            ".desc{color:#b8bcd0;}"
            "</style></head><body>"
            f"{''.join(cards)}</body></html>"
        )

    @staticmethod
    def _schedule_cleanup(path: Any) -> None:
        """延迟删除渲染临时文件（消息发送完成后清理，防磁盘残留）"""
        import os

        def _cleanup() -> None:
            try:
                os.remove(str(path))
            except OSError:
                pass

        try:
            asyncio.get_running_loop().call_later(_TMP_RETENTION, _cleanup)
        except RuntimeError:
            pass  # 无运行事件循环时交由系统临时目录回收

    # ---- 文本兜底（方案 A + C） ----

    @classmethod
    def _build_help_text(cls, plugin_commands: dict[str, dict]) -> str:
        """纯文本输出（渲染不可用时的兜底）；行数超阈值折叠为仅命令名"""
        sorted_plugins = cls._sorted_plugins(plugin_commands)
        total_rows = sum(len(info["commands"]) for _, info in sorted_plugins)
        folded = total_rows > _TEXT_FOLD_LIMIT

        lines: list[str] = ["可用命令:"]
        current_category: str | None = None
        for plugin_name, info in sorted_plugins:
            category = info["category"] or "未分类"
            if category != current_category:
                current_category = category
                lines.append(f"\n━━━ {current_category} ━━━")
            cmds = info["commands"]
            if not cmds:
                continue
            lines.append(f"  [{plugin_name}]")
            for cmd, aliases, desc in cmds:
                if folded:
                    lines.append(f"    /{cmd}")
                else:
                    alias_part = f"（别名：{'/'.join(aliases)}）" if aliases else ""
                    suffix = f" - {desc}" if desc else ""
                    lines.append(f"    /{cmd}{alias_part}{suffix}")
        if folded:
            lines.append("\n（命令较多已折叠，可用 /help <插件名> 查看单个插件）")
        return "\n".join(lines)
