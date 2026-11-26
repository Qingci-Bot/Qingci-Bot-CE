# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""内置管理插件 - 权限控制、开关、黑名单（Matcher 新式 API 示例）

使用 NoneBot2 风格的 Matcher/Rule/Permission 系统：
- on_command("status", permission=ADMIN): 普通管理员命令
- on_command("blacklist", permission=SUPERUSER): 超级管理员命令
- handler 接收 MatcherContext，通过 ctx.bot/plugin/matcher 访问依赖
"""

import asyncio
import logging
import time

from ...base import PluginBase
from ...matcher import MatcherContext, on_command
from ...permission import ADMIN, EVERYONE, SUPERUSER
from ...subcommands import with_subcommands as _with_subcommands

logger = logging.getLogger("qingci-bot.plugin.admin")


def _group_id_int(value: str | int | None) -> int:
    """把 group_id 收敛成 int（缓存键是 int，QQ 适配器给的是字符串）

    非数字（理论上不该出现）返回 0：缓存失效是「尽力而为」的优化，
    不能因为一个畸形 id 让 /group 报错。
    """
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


class AdminPlugin(PluginBase):
    """管理命令插件（Matcher 新式 API）"""

    name = "admin"
    category = "管理"
    version = "2.0.0"
    author = "Qingci-Bot CE"
    description = "管理命令插件：开关、清除对话、状态查询（Matcher API）"

    # LLM 可用性缓存（避免每次 /status 都消耗 token）
    _llm_check_cache: tuple[bool, float] = (False, 0.0)  # (ok, timestamp)
    _LLM_CACHE_TTL = 60  # 缓存 60 秒

    async def on_load(self):
        logger.info("管理插件已加载")
        self._config_lock = asyncio.Lock()

        # 注册 Matcher（handler 为 self 的方法，可访问 self.config/self.llm 等）
        self.matchers.append(
            on_command(
                "clear",
                permission=ADMIN,
                priority=1,
                description="清除当前会话的对话历史",
            )(self._cmd_clear)
        )
        self.matchers.append(
            on_command(
                "status",
                permission=ADMIN,
                priority=1,
                description="查看 Bot 运行状态",
            )(self._cmd_status)
        )
        # /whoami：查自己的身份识别码。原先这串 ID 藏在 /help 的「另有 N 条命令」
        # 提示里，既难找又与帮助无关——非 OneBot 平台（QQ 官方机器人）下它是
        # openid 字符串，管理员据此配置 admin_users，值得有独立命令。
        self.matchers.append(
            on_command(
                ("whoami", "id", "我是谁"),
                permission=EVERYONE,
                priority=1,
                description="查看身份识别码与当前权限",
            )(self._cmd_whoami)
        )
        self.matchers.append(
            _with_subcommands(
                on_command(
                    ("blacklist", "黑名单"),
                    permission=SUPERUSER,
                    priority=1,
                    description="黑名单管理",
                )(self._cmd_blacklist),
                {"add <用户ID>": "加入黑名单", "remove <用户ID>": "移出黑名单"},
            )
        )
        self.matchers.append(
            _with_subcommands(
                on_command(
                    "filter",
                    permission=SUPERUSER,
                    priority=1,
                    description="敏感词过滤开关",
                )(self._cmd_filter),
                {"on": "开启过滤", "off": "关闭过滤", "reload": "重载词库文件"},
            )
        )
        self.matchers.append(
            _with_subcommands(
                on_command(
                    "group",
                    permission=SUPERUSER,
                    priority=1,
                    description="当前群 Bot 开关",
                )(self._cmd_group),
                {"on": "允许本群使用", "off": "禁止本群使用"},
            )
        )

    async def on_unload(self):
        logger.info("管理插件已卸载")

    # ============ Matcher handlers ============

    async def _cmd_clear(self, ctx: MatcherContext) -> str:
        """清除对话历史"""
        if self.llm:
            await self.llm.clear_session(
                message_type=ctx.message_type,
                group_id=ctx.group_id,
                user_id=ctx.user_id,
            )
        return "对话历史已清除。"

    async def _cmd_status(self, ctx: MatcherContext) -> str:
        """查看 Bot 状态：按实际启用的适配器逐条列出

        早期只支持 OneBot，这里写死了「OneBot 连接」。接入多平台后，在 QQ 官方
        机器人 / Telegram 实例下这条永远显示「离线」——用户看到的是「明明在线
        却说离线」，比不显示更糟。改为遍历 ``bot.platforms``（只含已启用的
        适配器），并带上未连上的原因（凭据缺失、鉴权失败等），让「离线」与
        「没配」可区分。
        """
        llm_ok = await self._check_llm_cached()
        msg_count = (await self.db.get_message_count()) if self.db else 0
        lines = ["Bot 状态:", *self._platform_lines()]
        lines.append(f"  LLM 服务: {'可用' if llm_ok else '不可用'}")
        lines.append(f"  消息记录: {msg_count} 条")
        return "\n".join(lines)

    async def _cmd_whoami(self, ctx: MatcherContext) -> str:
        """显示当前用户的身份识别码与权限等级

        为什么单独成命令：QQ 官方机器人拿不到 QQ 号，身份是 openid 字符串
        （形如 o1d651883880739159623）。管理员要把这串填进 bot.admin_users
        才能授权，而它此前只出现在 /help 的「另有 N 条命令」提示里——用户
        按 /help 找授权方式，等于在帮助里找身份。
        """
        bot_cfg = getattr(self.bot, "config", None)
        bot_cfg = getattr(bot_cfg, "bot", None)

        level = "普通用户"
        if bot_cfg is not None:
            uid = str(ctx.user_id or "")
            super_admin = str(getattr(bot_cfg, "super_admin", "") or "")
            admins = {str(x) for x in (getattr(bot_cfg, "admin_users", None) or [])}
            if uid and uid == super_admin:
                level = "超级管理员"
            elif uid in admins:
                level = "管理员"

        platform = str((ctx.raw_event or {}).get("platform") or "未知")
        lines = [
            "身份信息",
            f"- 平台：{platform}",
            f"- 用户 ID：{ctx.user_id}",
        ]
        if ctx.group_id:
            lines.append(f"- 群 ID：{ctx.group_id}")
        lines.append(f"- 权限：{level}")
        if level == "普通用户":
            lines.append(
                "如需管理权限，请把上面的用户 ID 填进 bot.admin_users"
                "（或 bot.super_admin），再重启 Bot。"
            )
        return "\n".join(lines)

    def _platform_lines(self) -> list[str]:
        """各平台连接状态（一行一个适配器）"""
        adapters = []
        for platform in getattr(self.bot, "platforms", {}).values():
            # 与 bot.get_status() 口径一致：停用的适配器不上报，
            # 否则「Telegram 实例下 OneBot 已关闭」也会列出来
            if getattr(platform, "enabled", True):
                adapters.append(platform)

        if not adapters:
            return ["  平台: 未启用（在「平台配置」里启用并填写凭据）"]

        lines = ["  平台:"]
        for platform in adapters:
            label = getattr(platform, "display_name", "") or getattr(platform, "name", "未知")
            online = bool(getattr(platform, "is_connected", False))
            # 未连上时的原因：区分「进程没起来」与「凭据没配」，后者用户自己能修
            reason = "" if online else getattr(platform, "_start_error", "")
            suffix = f"（{reason}）" if reason else ""
            lines.append(
                f"    · {label} [{getattr(platform, 'name', '')}] "
                f"{'在线' if online else '离线'}{suffix}"
            )
        return lines

    async def _check_llm_cached(self) -> bool:
        """检查 LLM 可用性（带 60 秒缓存，避免频繁消耗 token）"""
        now = time.time()
        ok, ts = self._llm_check_cache
        if now - ts < self._LLM_CACHE_TTL:
            return ok
        if self.llm:
            ok = await self.llm.check_availability()
        else:
            ok = False
        self._llm_check_cache = (ok, now)
        return ok

    async def _cmd_blacklist(self, ctx: MatcherContext) -> str:
        """黑名单管理: /blacklist add/remove <用户ID>"""
        assert self.config is not None  # 运行时由 on_bot_connect 注入，仅类型收缩
        args = ctx.args.strip()
        if not args:
            return "格式: /blacklist add/remove <用户ID>"

        parts = args.split()
        if len(parts) < 2:
            return "格式: /blacklist add/remove <用户ID>"

        action = parts[0]
        target = parts[1].strip()
        if not target:
            return "格式: /blacklist add/remove <用户ID>"

        cfg = self.config.bot
        if action == "add":
            if target not in cfg.user_blacklist:
                cfg.user_blacklist.append(target)
                try:
                    await self._save_config_async()
                except Exception:
                    cfg.user_blacklist.remove(target)
                    logger.exception("保存配置失败，已回滚")
                    return "保存配置失败，请稍后再试。"
                return f"已将 {target} 加入黑名单。"
            return f"{target} 已在黑名单中。"
        elif action == "remove":
            if target in cfg.user_blacklist:
                cfg.user_blacklist.remove(target)
                try:
                    await self._save_config_async()
                except Exception:
                    cfg.user_blacklist.append(target)
                    logger.exception("保存配置失败，已回滚")
                    return "保存配置失败，请稍后再试。"
                return f"已将 {target} 移出黑名单。"
            return f"{target} 不在黑名单中。"

        return "格式: /blacklist add/remove <用户ID>"

    async def _cmd_filter(self, ctx: MatcherContext) -> str:
        """敏感词过滤管理: /filter on|off|reload"""
        assert self.config is not None  # 运行时由 on_bot_connect 注入，仅类型收缩
        action = ctx.args.strip().lower()
        if action == "on":
            self.config.filter.enabled = True
            try:
                await self._save_config_async()
            except Exception:
                self.config.filter.enabled = False
                logger.exception("保存配置失败，已回滚")
                return "保存配置失败，请稍后再试。"
            return "已开启敏感词过滤。" + self._filter_empty_warning()
        if action == "off":
            self.config.filter.enabled = False
            try:
                await self._save_config_async()
            except Exception:
                self.config.filter.enabled = True
                logger.exception("保存配置失败，已回滚")
                return "保存配置失败，请稍后再试。"
            return "已关闭敏感词过滤。"
        if action == "reload":
            sensitive_filter = getattr(self.bot, "sensitive_filter", None)
            if sensitive_filter is None:
                return "敏感词过滤器未初始化。"
            # 同步读文件的重载丢到线程池，避免阻塞事件循环
            await asyncio.to_thread(sensitive_filter.reload)
            if not sensitive_filter.words:
                # 空词库时 reload 成功但过滤不会生效，明确提示用户
                return (
                    "敏感词库已重新加载，但词库为空，过滤不会生效。"
                    "请编辑 data/sensitive_words.txt（一行一词）。"
                )
            return f"敏感词库已重新加载，当前 {len(sensitive_filter.words)} 个词。"
        return "格式: /filter on|off|reload"

    def _filter_empty_warning(self) -> str:
        """词库为空时返回用户可见提示（开启成功但过滤形同虚设）"""
        sensitive_filter = getattr(self.bot, "sensitive_filter", None)
        if sensitive_filter is not None and not sensitive_filter.words:
            return "注意：词库为空，请编辑 data/sensitive_words.txt（一行一词）。"
        return ""

    async def _cmd_group(self, ctx: MatcherContext) -> str:
        """当前群 Bot 开关: /group on|off"""
        if ctx.message_type != "group" or not ctx.group_id:
            return "该命令只能在群聊中使用。"
        action = ctx.args.strip().lower()
        if action not in ("on", "off"):
            return "格式: /group on|off"
        enabled = action == "on"
        # 保留群已有的 trigger_mode，仅变更开关
        trigger_mode = None
        if self.db:
            try:
                existing = await self.db.get_group_config(ctx.group_id)
                if existing:
                    trigger_mode = existing.get("trigger_mode")
                await self.db.upsert_group_config(ctx.group_id, enabled, trigger_mode)
            except Exception:
                logger.exception(f"保存群配置失败: group_id={ctx.group_id}")
                return "保存群配置失败，请稍后再试。"
        # 失效 chat 插件的群配置缓存，使变更立即生效
        # 注意是 `..chat`（builtin 下的兄弟包），写成 `.chat` 会去找
        # bot.plugin.builtin.admin.chat——不存在的模块，`/group` 直接 ImportError
        from ..chat import invalidate_group_config_cache

        invalidate_group_config_cache(_group_id_int(ctx.group_id))
        return f"本群 Bot 已{'开启' if enabled else '关闭'}。"

    async def _save_config_async(self):
        """异步保存配置（加锁防止并发写）"""
        async with self._config_lock:
            await asyncio.to_thread(self.config.save)
