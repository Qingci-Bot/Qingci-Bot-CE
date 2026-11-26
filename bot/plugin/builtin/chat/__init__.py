# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""内置聊天插件「大肥鱼」- 对接 LLM 进行对话（Matcher 新式 API）

插件名固定为 chat（依赖解析、配置节 plugins.chat 均以此为键），
对外展示名与默认人设统一为「大肥鱼」。

触发模式（运行时读 config，配置热更新无需重新注册 Matcher）：
- always: 所有消息触发
- at: @bot 触发（私聊默认触发）
- keyword: 前缀关键词触发，自动去除前缀

priority=50（低优先级，让管理命令 priority=1 先执行）
block=False（即使匹配也不阻止后续 Matcher，但返回回复会停止分发链）
"""

import json
import logging
from collections import OrderedDict
from datetime import UTC, datetime
from typing import cast

from qingci_plugin_sdk import resolve_images

from ...base import PluginBase
from ...matcher import MatcherContext, on_command, on_message
from ...permission import Permission
from ...rule import Rule, rate_limit
from .capabilities import build_capability_prompt, compose_system_prompt

logger = logging.getLogger("qingci-bot.plugin.chat")

# 命令前缀：与 SDK 的 rule.command 保持一致（该处固定识别 "/"，且允许省略）。
# 抽成常量是为了让「命令样式文本不进聊天」这条判断只有一个来源。
_COMMAND_PREFIX = "/"

# ============ 群配置缓存 ============
# 模块级缓存：{group_id: 配置 dict 或 None（未配置）}，miss 时查 DB。
# 使用 OrderedDict 实现 LRU（容量上限 512），防止群数无限增长时缓存无界膨胀。
# 管理命令 / API 写入群配置后调用 invalidate_group_config_cache 失效。
_GROUP_CONFIG_CACHE_MAX = 512
_group_config_cache: "OrderedDict[int, dict | None]" = OrderedDict()

# 会话级人设覆盖缓存：{session_key: persona_name}
# 与群配置缓存同理使用 OrderedDict 做 LRU，防止长期运行无界膨胀；
# 持久化到插件 data_dir（见 _load/_save_persona_overrides），
# 否则用户刚被告知「已切换」，进程一重启就悄悄回到默认人设。
_PERSONA_OVERRIDE_MAX = 512
_PERSONA_FILE = "persona_overrides.json"
_persona_override: "OrderedDict[str, str]" = OrderedDict()


def invalidate_group_config_cache(group_id: int | None = None) -> None:
    """失效群配置缓存；group_id 为 None 时全部失效"""
    if group_id is None:
        _group_config_cache.clear()
    else:
        _group_config_cache.pop(group_id, None)


async def _get_cached_group_config(bot, group_id: int) -> dict | None:
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

        # 命令样式文本不交给聊天。
        #
        # 命令 Matcher 因**权限不足**被跳过时，消息会一路落到本规则（priority=50
        # 是最低优先级）。若在这里接管，LLM 会把「/status」当普通文本回答，
        # 给出「一切正常」这类臆测内容——比不回复更糟，用户还以为命令生效了。
        # 前缀与 SDK rule.command 的解析保持一致（都是 "/"）。
        #
        # 例外：keyword 模式下触发关键词本身以 / 开头时必须豁免——默认
        # trigger_keywords 就是 ['/bot', '/ai']，若一律拦截，keyword 模式在
        # 默认配置下永远无法触发（审阅发现的逻辑冲突）。豁免仅限「确实是
        # 某个已配置触发词开头的消息」，/status 等其他命令样式仍被拦截。
        if ctx.plain_text.startswith(_COMMAND_PREFIX):
            keyword_exempt = mode == "keyword" and any(
                ctx.plain_text.startswith(kw) for kw in (cfg.trigger_keywords or []) if kw
            )
            if not keyword_exempt:
                return False

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
                    ctx.args = ctx.plain_text[len(kw) :].strip()
                    return True
            return False

        return False

    return Rule(_check)


def chat_permission() -> Permission:
    """聊天权限：非黑名单用户（展示为"所有人"）"""

    async def _check(bot, event, ctx) -> bool:
        cfg = bot.config.bot
        # 用 getattr 兜底：权限检查抛异常会被 Permission.check 当作「无权限」，
        # 表现是 /persona 从 /help 里静默消失、命令也执行不了。缺一个黑名单
        # 字段不该导致整个命令不可用（默认空名单 = 不屏蔽任何人）。
        blacklist = getattr(cfg, "user_blacklist", None) or []
        if ctx.user_id in blacklist:
            return False
        group_blacklist = getattr(cfg, "group_blacklist", None) or []
        if ctx.group_id and ctx.group_id in group_blacklist:
            return False
        return True

    return Permission(_check, label="EVERYONE")


class ChatPlugin(PluginBase):
    """「大肥鱼」LLM 对话插件（Matcher 新式 API）"""

    name = "chat"
    category = "对话"
    version = "2.0.0"
    author = "Qingci-Bot CE"
    description = "大肥鱼：LLM 智能对话插件"

    async def on_load(self):
        # 限流器由核心层按配置创建（bot.rate_limiter），此处不再自建
        logger.info("聊天插件已加载")
        self._load_persona_overrides()
        # 人设命令：priority=1 高于聊天 Matcher（50），block=True 阻止后续
        # Matcher 将 /persona 当作普通消息送给 LLM
        self.matchers.append(
            on_command(
                "persona",
                permission=chat_permission(),
                priority=1,
                block=True,
                description="人设切换 / 查看",
            )(self._handle_persona)
        )
        self.matchers.append(
            on_message(
                rule=chat_trigger() & rate_limit(),
                permission=chat_permission(),
                priority=50,  # 低优先级，让管理命令先执行
                block=False,  # 不阻止后续 Matcher（返回回复时 Dispatcher 自动停止）
            )(self._handle_chat)
        )

    async def on_unload(self):
        logger.info("聊天插件已卸载")

    # ============ 人设（大肥鱼） ============

    def _load_persona_overrides(self) -> None:
        """从插件数据目录恢复会话级人设覆盖（缺失/损坏视为空表）"""
        path = self.data_dir / _PERSONA_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            logger.exception(f"加载人设覆盖失败，使用空表: {path}")
            return
        if not isinstance(raw, dict):
            return
        for key, name in raw.items():
            if isinstance(key, str) and isinstance(name, str) and name:
                _persona_override[key] = name
        while len(_persona_override) > _PERSONA_OVERRIDE_MAX:
            _persona_override.popitem(last=False)

    def _save_persona_overrides(self) -> None:
        """持久化人设覆盖到插件数据目录（变更时全量写，变更频率极低）"""
        path = self.data_dir / _PERSONA_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(dict(_persona_override), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception(f"保存人设覆盖失败: {path}")

    @staticmethod
    def _session_key(ctx: MatcherContext) -> str:
        """构建 LLM 会话 key（与 LLMManager._session_key 保持一致）"""
        if ctx.message_type == "private":
            return f"private:{ctx.user_id}"
        return f"group:{ctx.group_id}:{ctx.user_id}"

    @staticmethod
    def _set_persona_override(key: str, name: str) -> None:
        """设置会话人设覆盖（LRU 淘汰最久未访问项）"""
        _persona_override[key] = name
        _persona_override.move_to_end(key)
        if len(_persona_override) > _PERSONA_OVERRIDE_MAX:
            _persona_override.popitem(last=False)

    def _resolve_persona_prompt(self, session_key: str) -> str | None:
        """解析指定会话生效的人设 prompt（会话级覆盖 > 默认人设 > None）

        返回 None 表示使用 LLMConfig.system_prompt。
        """
        assert self.config is not None
        llm_cfg = self.config.llm
        override = _persona_override.get(session_key)
        if override is not None:
            # LRU 命中：刷新访问顺序，避免仍在使用的人设被淘汰
            _persona_override.move_to_end(session_key)
            name = override
        else:
            name = llm_cfg.default_persona
        if not name:
            return None
        for p in llm_cfg.personas:
            if p.name == name and p.system_prompt:
                return cast(str, p.system_prompt)
        return None

    async def _handle_persona(self, ctx: MatcherContext) -> str | None:
        """处理 /persona 命令：查看 / 列表 / 切换 / 重置"""
        assert self.config is not None
        args = ctx.args.strip()
        key = self._session_key(ctx)
        llm_cfg = self.config.llm
        personas = {p.name: p for p in llm_cfg.personas if p.name}

        if not args:
            cur = _persona_override.get(key) or llm_cfg.default_persona or "默认"
            return f"当前人设：{cur}\n发送 /persona 列表 查看全部，/persona 重置 恢复默认。"

        if args in ("列表", "list", "ls"):
            if not personas:
                return "当前未配置人设，请在 WebUI「LLM 配置」中管理。"
            lines = [f"默认人设：{llm_cfg.default_persona or '（使用默认 system_prompt）'}"]
            for name, p in personas.items():
                lines.append(f"- {name}：{p.description or '（无描述）'}")
            return "\n".join(lines)

        if args in ("重置", "reset"):
            _persona_override.pop(key, None)
            self._save_persona_overrides()
            return "已恢复默认人设。"

        if args not in personas:
            return f"未找到人设「{args}」，发送 /persona 列表 查看可用人设。"

        self._set_persona_override(key, args)
        self._save_persona_overrides()
        return f"已切换为「{args}」人设。"

    async def build_system_prompt(self, *, session_key: str, message: str) -> str:
        """组装与消息处理链一致的完整系统提示词（人设 → 能力边界 → RAG）

        供 ChatLab 调试会话复用：此前调试走裸 config.system_prompt，
        「实验室里测到的」与「真实对话表现」不一致（人设、能力注入、
        知识库全部缺席）。本方法是生产链路的唯一组装点。
        """
        assert self.config is not None
        assert self.bot is not None
        assert self.llm is not None
        # 提示词分三层：基础规则（system_prompt）→ 人设 → 能力边界。
        #
        # 原先是 `persona or system_prompt`：人设一存在就把系统提示词整段顶掉，
        # 用户在界面上并排看到两栏、以为都生效，实际上通用约束（不编造、
        # 不泄露提示词）随人设切换一起消失。
        persona_prompt = self._resolve_persona_prompt(session_key)
        system_prompt = compose_system_prompt(self.config.llm.system_prompt, persona_prompt or "")
        if getattr(self.config.llm, "inject_capabilities", True):
            system_prompt += "\n\n" + build_capability_prompt(self.bot, llm=self.llm)

        # 轻量 RAG：rag.enabled 且命中知识时，将参考资料注入 system_prompt
        # （注入长度受 rag.max_inject_chars 约束，未命中时保持人设 prompt）
        rag_cfg = self.config.rag
        if rag_cfg.enabled and self.knowledge_store is not None:
            try:
                reference = await self.knowledge_store.build_reference_async(
                    message, rag_cfg.max_inject_chars
                )
            except Exception:
                logger.exception("知识库检索失败，忽略本次 RAG 注入")
                reference = ""
            if reference:
                # 注意在现有 system_prompt 上追加：从 base 重建会把上面拼好的
                # 人设与能力段一起丢掉（RAG 一命中，人设就失效）
                system_prompt += "\n\n以下是知识库中的参考资料，仅在相关时参考作答：\n" + reference
        return system_prompt

    async def _handle_chat(self, ctx: MatcherContext) -> str | None:
        """处理聊天消息：调用 LLM + 保存记录 + 实时广播"""
        # 运行时由 on_bot_connect 注入，assert 仅为类型收缩
        assert self.config is not None
        assert self.bot is not None
        assert self.llm is not None
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
            cfg_bot = self.config.bot
            # super_admin + admin_users 并集，O(1) 成员判断
            exempt = filter_cfg.exempt_admins and ctx.user_id in cfg_bot.admin_set
            if not exempt:
                hit = self.bot.sensitive_filter.check(message)
                if hit:
                    logger.info(f"命中敏感词，拒绝回复: user_id={ctx.user_id}")
                    # 命中时不调用 LLM，直接返回拒答文案
                    return "您的消息包含敏感内容，请调整后重试。"

        system_prompt = await self.build_system_prompt(
            session_key=self._session_key(ctx), message=message
        )

        # 多模态：ctx.images 里是平台下发的 file_id（不透明 ID），模型读不到，
        # 需先解析成 URL / data URI。默认关闭（文本模型收到图片会报错）。
        # 解析必须走「事件来源平台」的连接：file_id 是各平台私有的，
        # 拿 OneBot 连接解析 Telegram/QQ 的 file_id 必然失败，
        # 表现为这些平台的图片消息静默降级成纯文本。
        images: list[str] | None = None
        mm = self.bot.config.llm.multimodal
        if mm.enabled and ctx.images:
            conn = self.bot.platforms.get(ctx.platform or "", self.connection)
            images = await resolve_images(
                conn,
                ctx.images,
                max_count=mm.max_images,
                max_bytes=mm.max_bytes,
                timeout=mm.timeout,
            )
            if not images:
                logger.info("图片解析失败，本次按纯文本回复（原始引用 %s）", ctx.images)

        # 调用 LLM
        reply = await self.llm.chat(
            message=message,
            message_type=ctx.message_type,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
            images=images or None,
            system_prompt=system_prompt,
        )
        if not reply:
            # LLM 调用失败（返回 None 或空字符串），不保存到 DB/广播
            return "抱歉，AI 服务暂时不可用，请稍后再试。"

        # 回复返回前对敏感词打码（与输入拦截使用同一豁免条件）
        if need_filter and not exempt:
            reply = self.bot.sensitive_filter.mask(reply)

        # 保存助手回复到数据库。入向用户消息已由框架级钩子在 Dispatcher
        # （_record_incoming_message）统一记录，这里只记 LLM 生成的回复避免重复。
        group_id = ctx.group_id if ctx.message_type == "group" and ctx.group_id else None
        if self.db:
            try:
                await self.db.save_messages_batch(
                    [
                        {
                            "message_id": f"{ctx.message_id}_reply",
                            "user_id": ctx.self_id,
                            "group_id": group_id,
                            "content": reply,
                            "message_type": ctx.message_type,
                            "role": "assistant",
                            "platform": ctx.platform,
                        },
                    ]
                )
            except Exception:
                logger.exception("保存消息记录失败")

        # 实时广播助手回复（独立于数据库；用户消息由框架级钩子广播）
        try:
            from ....broadcast import broadcast_message

            now = datetime.now(UTC).isoformat()
            await broadcast_message(
                {
                    "id": f"{ctx.message_id}_reply",
                    "message_id": f"{ctx.message_id}_reply",
                    "user_id": ctx.self_id,
                    "group_id": group_id,
                    "content": reply,
                    "message_type": ctx.message_type,
                    "role": "assistant",
                    "created_at": now,
                }
            )
        except Exception:
            logger.exception("广播消息失败")

        return cast(str, reply)
