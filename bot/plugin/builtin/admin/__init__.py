"""内置管理插件 - 权限控制、开关、黑名单（Matcher 新式 API 示例）

使用 NoneBot2 风格的 Matcher/Rule/Permission 系统：
- on_command("clear", permission=SUPERUSER): 命令匹配 + 权限控制
- handler 接收 MatcherContext，通过 ctx.bot/plugin/matcher 访问依赖
"""

import asyncio
import logging
import time

from ...base import PluginBase
from ...matcher import MatcherContext, on_command
from ...permission import SUPERUSER

logger = logging.getLogger("qingci-bot.plugin.admin")


class AdminPlugin(PluginBase):
    """管理命令插件（Matcher 新式 API）"""

    name = "admin"
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
                permission=SUPERUSER,
                priority=1,
                description="清除当前会话的对话历史",
            )(self._cmd_clear)
        )
        self.matchers.append(
            on_command(
                "status",
                permission=SUPERUSER,
                priority=1,
                description="查看 Bot 运行状态",
            )(self._cmd_status)
        )
        self.matchers.append(
            on_command(
                ("blacklist", "黑名单"),
                permission=SUPERUSER,
                priority=1,
                description="黑名单管理: /blacklist add/remove <QQ号>",
            )(self._cmd_blacklist)
        )
        self.matchers.append(
            on_command(
                "filter",
                permission=SUPERUSER,
                priority=1,
                description="敏感词过滤开关: /filter on|off|reload",
            )(self._cmd_filter)
        )
        self.matchers.append(
            on_command(
                "group",
                permission=SUPERUSER,
                priority=1,
                description="当前群 Bot 开关: /group on|off",
            )(self._cmd_group)
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
        """查看 Bot 状态"""
        connected = self.connection.is_connected if self.connection else False
        llm_ok = await self._check_llm_cached()
        msg_count = (await self.db.get_message_count()) if self.db else 0
        return (
            f"Bot 状态:\n"
            f"  OneBot 连接: {'在线' if connected else '离线'}\n"
            f"  LLM 服务: {'可用' if llm_ok else '不可用'}\n"
            f"  消息记录: {msg_count} 条"
        )

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

        return "格式: /blacklist add/remove <QQ号>"

    async def _cmd_filter(self, ctx: MatcherContext) -> str:
        """敏感词过滤管理: /filter on|off|reload"""
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
        from .chat import invalidate_group_config_cache

        invalidate_group_config_cache(ctx.group_id)
        return f"本群 Bot 已{'开启' if enabled else '关闭'}。"

    async def _save_config_async(self):
        """异步保存配置（加锁防止并发写）"""
        async with self._config_lock:
            await asyncio.to_thread(self.config.save)
