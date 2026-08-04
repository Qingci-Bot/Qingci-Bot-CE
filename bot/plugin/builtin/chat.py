"""内置聊天插件 - 对接 LLM 进行对话（Matcher 新式 API）

触发模式（运行时读 config，配置热更新无需重新注册 Matcher）：
- always: 所有消息触发
- at: @bot 触发（私聊默认触发）
- keyword: 前缀关键词触发，自动去除前缀

priority=50（低优先级，让管理命令 priority=1 先执行）
block=False（即使匹配也不阻止后续 Matcher，但返回回复会停止分发链）
"""

import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from ..base import PluginBase
from ..matcher import MatcherContext, on_message
from ..permission import Permission
from ..ratelimit import RateLimiter
from ..rule import Rule, rate_limit

logger = logging.getLogger("qingci-bot.plugin.chat")

# ============ 群配置缓存 ============
# 模块级缓存：{group_id: 配置 dict 或 None（未配置）}，miss 时查 DB。
# 使用 OrderedDict 实现 LRU（容量上限 512），防止群数无限增长时缓存无界膨胀。
# 管理命令 / API 写入群配置后调用 invalidate_group_config_cache 失效。
_GROUP_CONFIG_CACHE_MAX = 512
_group_config_cache: "OrderedDict[int, Optional[dict]]" = OrderedDict()


def invalidate_group_config_cache(group_id: Optional[int] = None) -> None:
    """失效群配置缓存；group_id 为 None 时全部失效"""
    if group_id is None:
        _group_config_cache.clear()
    else:
        _group_config_cache.pop(group_id, None)


async def _get_cached_group_config(bot, group_id: int) -> Optional[dict]:
    """获取群配置（缓存优先，miss 查 DB；LRU 淘汰最久未访问项）"""
    if group_id in _group_config_cache:
        # 命中：移到尾部标记为最近使用
        _group_config_cache.move_to_end(group_id)
        return _group_config_cache[group_id]
    row = None
    if bot is not None and getattr(bot, "db", None) is not None:
        try:
            row = await bot.db.get_group_config(group_id)
        except Exception:
            logger.exception(f"查询群配置失败: group_id={group_id}")
            # 查询失败不写缓存，允许下次重试
            return None
    _group_config_cache[group_id] = row
    # 超限时淘汰最久未访问项（LRU）
    if len(_group_config_cache) > _GROUP_CONFIG_CACHE_MAX:
        _group_config_cache.popitem(last=False)
    return row


def chat_trigger() -> Rule:
    """聊天触发规则（运行时读配置，群配置优先于全局）

    群消息先查群粒度配置（缓存 + DB）：
    - enabled=False 直接不触发
    - trigger_mode 非空时覆盖全局触发模式
    私聊不查群配置。

    匹配后将实际消息文本写入 ctx.args：
    - always/at: ctx.args = ctx.plain_text
    - keyword: ctx.args = 去除前缀后的文本
    """

    async def _check(bot, event, ctx) -> bool:
        if not ctx.plain_text:
            return False

        cfg = bot.config.bot
        mode = cfg.trigger_mode

        # 群粒度配置（仅群消息查询，私聊不受影响）
        if ctx.message_type == "group" and ctx.group_id:
            group_cfg = await _get_cached_group_config(bot, ctx.group_id)
            if group_cfg is not None:
                if not group_cfg.get("enabled", True):
                    return False
                if group_cfg.get("trigger_mode"):
                    mode = group_cfg["trigger_mode"]

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
        # 惰性创建限流器并挂到 bot（守卫：核心层后续批次若已创建则不重复）
        bot = self.bot
        if bot is not None and getattr(bot, "rate_limiter", None) is None:
            rl_cfg = bot.config.rate_limit
            bot.rate_limiter = RateLimiter(
                daily_limit=rl_cfg.daily_limit,
                cooldown_seconds=rl_cfg.cooldown_seconds,
            )
        logger.info("聊天插件已加载")
        self.matchers.append(
            on_message(
                rule=chat_trigger() & rate_limit(),
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

        # 敏感词过滤（filter.enabled 开关控制；管理员可按 exempt_admins 豁免）
        filter_cfg = self.config.filter
        need_filter = (
            filter_cfg.enabled
            and self.bot is not None
            and getattr(self.bot, "sensitive_filter", None) is not None
        )
        exempt = False
        if need_filter:
            exempt = (
                filter_cfg.exempt_admins
                and ctx.user_id in self.config.bot.admin_users
            )
            if not exempt:
                hit = self.bot.sensitive_filter.check(message)
                if hit:
                    logger.info(f"命中敏感词，拒绝回复: user_id={ctx.user_id}")
                    # 命中时不调用 LLM，直接返回拒答文案
                    return "您的消息包含敏感内容，请调整后重试。"

        # 轻量 RAG：rag.enabled 且命中知识时，将参考资料注入 system_prompt
        # （注入长度受 rag.max_inject_chars 约束，未命中时保持原有行为）
        system_prompt = None
        rag_cfg = self.config.rag
        if rag_cfg.enabled and self.knowledge_store is not None:
            try:
                reference = self.knowledge_store.build_reference(
                    message, rag_cfg.max_inject_chars
                )
            except Exception:
                logger.exception("知识库检索失败，忽略本次 RAG 注入")
                reference = ""
            if reference:
                system_prompt = (
                    self.config.llm.system_prompt
                    + "\n\n以下是知识库中的参考资料，仅在相关时参考作答：\n"
                    + reference
                )

        # 调用 LLM
        reply = await self.llm.chat(
            message=message,
            message_type=ctx.message_type,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
            images=ctx.images or None,
            system_prompt=system_prompt,
        )
        if not reply:
            # LLM 调用失败（返回 None 或空字符串），不保存到 DB/广播
            return "抱歉，AI 服务暂时不可用，请稍后再试。"

        # 回复返回前对敏感词打码（与输入拦截使用同一豁免条件）
        if need_filter and not exempt:
            reply = self.bot.sensitive_filter.mask(reply)

        # 保存消息记录到数据库
        group_id = ctx.group_id if ctx.message_type == "group" and ctx.group_id else None
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
            now = datetime.now(timezone.utc).isoformat()
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
