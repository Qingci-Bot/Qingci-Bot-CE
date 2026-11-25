# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""内置记忆插件「大肥鱼记得你」- 上下文与记忆的唯一负责人

## 它接管了什么

1.23 之前，「上下文管理」（按条数裁剪、超 token 就让模型摘要压缩）写死在
``LLMManager`` 里。本插件把这份能力接过来：框架只保留一个注入点
（``LLMManager.set_memory_provider``），裁剪与压缩策略、记忆分层、演化规则
全部在插件侧。框架剩下的只是「按插件的决定把会话表写一致」——那是存储
一致性问题，不是记忆策略问题。

内置插件不可移除（见 ``bot/plugin/_loading._BUILTIN_PLUGINS``），所以不必担心
「插件不在时记忆能力凭空消失」；即使加载异常，框架也会退回只做硬裁剪的
``FallbackMemoryProvider`` 兜底，保证上下文不会无限增长。

## 三层记忆

| 层 | 载体 | 生命周期 |
| --- | --- | --- |
| 短期 | 会话窗口（框架内存 + 会话表），本插件参与裁剪/摘要 | 会话内；``/clear`` 即清 |
| 长期 | ``memory.db`` 的 ``tier='long'`` | 随久未命中指数衰减，低于下限遗忘 |
| 永久 | ``memory.db`` 的 ``tier='permanent'`` | 不衰减不遗忘，只能显式删除 |

## 自主演化（四件事，均在后台进行，不阻塞回复）

1. **强化**：被召回并实际注入提示词 → 命中数 +1、显著性上调
2. **遗忘**：长期记忆按半衰期衰减，低于下限自动删除（永久层豁免）
3. **晋升**：反复命中且足够重要的长期记忆自动升为永久（可关）
4. **归并**：高度相似的记忆合并，保留更重要的一条，命中数累加

## 配置（``plugins.memory``，全部有默认值，可整节省略）

见 ``docs/CONFIGURATION.md``「记忆插件」一节。生产误配不会致命：任何一项
解析失败都退回默认值并记 WARNING，记忆功能降级而不是把 bot 拖挂。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ....llm.memory import HistoryDecision, SummaryPolicy
from ...base import PluginBase
from ...matcher import MatcherContext, on_command
from ...permission import EVERYONE
from .extract import Fact, extract_facts
from .store import SCOPE_USER, TIER_LONG, TIER_PERMANENT, Memory, MemoryStore, similarity

logger = logging.getLogger("qingci-bot.plugin.memory")

# 注入提示词的记忆块标题：放在系统提示词末尾，尽量贴近模型注意力的末端
_INJECT_HEADER = (
    "\n\n[记忆] 你记得关于对方的这些事（按重要度排序）。"
    "它们来自过去的对话，可能已经过时——与当前消息冲突时，一律以当前消息为准：\n"
)


@dataclass(slots=True)
class MemorySettings:
    """``plugins.memory`` 配置（每项都有安全默认值）"""

    enabled: bool = True
    # 单次注入提示词的最大字符数：记忆是"锦上添花"，不能把对话预算吃光
    max_inject_chars: int = 600
    # 单次召回条数上限
    recall_limit: int = 8
    # 是否从用户消息里提炼长期记忆
    extract: bool = True
    # 反复命中的长期记忆是否自动升为永久
    auto_promote: bool = True
    # 长期记忆半衰期（天）：越小忘得越快
    decay_half_life_days: float = 30.0
    # 显著性低于该值即遗忘
    decay_floor: float = 0.15
    # 演化（衰减/晋升/归并）节流间隔（秒）
    evolve_interval_seconds: int = 3600
    # 相似度达到该值即视为重复并合并
    merge_threshold: float = 0.9

    @classmethod
    def from_config(cls, raw: Any) -> MemorySettings:
        """从 ``plugins.memory`` 字典解析；类型不对的项退回默认值"""
        if not isinstance(raw, dict):
            return cls()
        defaults = cls()
        s = cls()
        for field_name in (
            "enabled",
            "extract",
            "auto_promote",
        ):
            if field_name in raw:
                setattr(s, field_name, bool(raw[field_name]))
        for field_name, caster in (
            ("max_inject_chars", int),
            ("recall_limit", int),
            ("evolve_interval_seconds", int),
            ("decay_half_life_days", float),
            ("decay_floor", float),
            ("merge_threshold", float),
        ):
            if field_name in raw:
                try:
                    setattr(s, field_name, caster(raw[field_name]))
                except (TypeError, ValueError):
                    logger.warning(
                        f"plugins.memory.{field_name} 解析失败，使用默认值 "
                        f"{getattr(defaults, field_name)!r}: {raw[field_name]!r}"
                    )
        # 明显不合理的值夹到可用区间，避免配置笔误把记忆彻底关掉
        s.max_inject_chars = max(0, s.max_inject_chars)
        s.recall_limit = max(0, s.recall_limit)
        s.decay_half_life_days = max(0.0, s.decay_half_life_days)
        s.decay_floor = min(max(s.decay_floor, 0.0), 1.0)
        s.merge_threshold = min(max(s.merge_threshold, 0.5), 1.0)
        s.evolve_interval_seconds = max(0, s.evolve_interval_seconds)
        return s


class MemoryPlugin(PluginBase):
    """记忆插件：短期/长期/永久三层 + 自主演化"""

    name = "memory"
    version = "1.0.0"
    author = "Qingci-Bot CE"
    description = "记忆管理：短期/长期/永久三层记忆，支持自主演化（强化·遗忘·晋升·归并）"

    def __init__(self) -> None:
        super().__init__()
        self._store: MemoryStore | None = None
        self._settings = MemorySettings()
        # 本轮召回的记忆 id，用于在回复成功后按"确实用上了"强化
        self._recalled: dict[str, list[int]] = {}

    # ============ 生命周期 ============

    async def on_load(self) -> None:
        raw: object = {}
        if self.config is not None:
            plugins_cfg = getattr(self.config, "plugins", None) or {}
            raw = plugins_cfg.get("memory") if isinstance(plugins_cfg, dict) else {}
        self._settings = MemorySettings.from_config(raw)

        self._store = MemoryStore(self.data_dir / "memory.db")
        # 触发一次建表，让配置/路径问题在加载期暴露，而不是等第一条消息
        await asyncio.to_thread(self._store.get_meta, "schema", "")

        if self._settings.enabled and self.llm is not None:
            setter = getattr(self.llm, "set_memory_provider", None)
            if callable(setter):
                setter(self)
            else:
                logger.warning("LLM 管理器缺少 set_memory_provider，记忆插件仅提供命令")
        elif not self._settings.enabled:
            logger.info("plugins.memory.enabled=false，记忆插件不参与对话（命令仍可用）")

        # matchers 由框架在加载前初始化，None 只存在于未加载状态
        assert self.matchers is not None
        self.matchers.append(
            on_command(
                "memory",
                permission=EVERYONE,
                priority=1,
                block=True,
                description="记忆管理: /memory list|add|pin|unpin|forget|search|stats|clear",
            )(self._cmd_memory)
        )
        logger.info(
            f"记忆插件已加载（注入上限 {self._settings.max_inject_chars} 字、"
            f"召回 {self._settings.recall_limit} 条、提炼={'开' if self._settings.extract else '关'}）"
        )

    async def on_unload(self) -> None:
        if self.llm is not None:
            setter = getattr(self.llm, "set_memory_provider", None)
            if callable(setter):
                # 让框架退回兜底实现，避免持有已卸载插件的引用
                setter(None)
        if self._store is not None:
            await asyncio.to_thread(self._store.close)
            self._store = None
        logger.info("记忆插件已卸载")

    # ============ MemoryProvider 实现 ============

    async def augment_prompt(
        self,
        *,
        session_key: str,
        user_id: int,
        group_id: int,
        query: str,
        system_prompt: str,
    ) -> str:
        """召回记忆并追加到系统提示词末尾"""
        store = self._store
        if store is None or not self._settings.enabled or not user_id:
            return system_prompt

        memories = await asyncio.to_thread(
            store.recall,
            scope=SCOPE_USER,
            scope_id=int(user_id),
            query=query,
            limit=self._settings.recall_limit,
        )
        if not memories:
            return system_prompt

        lines: list[str] = []
        used: list[Memory] = []
        budget = self._settings.max_inject_chars
        consumed = 0
        for m in memories:
            piece = f"- {m.content}"
            if consumed + len(piece) > budget:
                break
            lines.append(piece)
            used.append(m)
            consumed += len(piece)
        if not lines:
            return system_prompt

        # 真正被注入的才计入"命中"——没进提示词的记忆不该因为被检索到就变重要
        self._recalled[session_key] = [m.id for m in used]
        self._cap_recalled()
        await asyncio.to_thread(store.reinforce, [m.id for m in used], delta=0.05)
        return system_prompt + _INJECT_HEADER + "\n".join(lines)

    async def manage_history(
        self,
        *,
        session_key: str,
        messages: list[dict],
        max_messages: int,
        max_tokens: int,
        summary: SummaryPolicy,
        estimate,
    ) -> HistoryDecision:
        """短期记忆管理：条数裁剪 → 摘要压缩 → token 硬裁剪兜底"""
        store = self._store
        msgs = list(messages)
        dropped: list[dict] = []

        # 1) 条数上限
        if max_messages > 0 and len(msgs) > max_messages:
            dropped.extend(msgs[: len(msgs) - max_messages])
            msgs = msgs[-max_messages:]

        rewrite = False

        # 2) 超 token 预算时优先摘要压缩（是否启用由框架给出的策略决定）
        if max_tokens > 0 and len(msgs) > 2 and estimate(msgs) > max_tokens:
            if summary.enabled and store is not None:
                compressed = await self._compress(session_key, msgs, summary)
                if compressed is not None:
                    dropped.extend(msgs[: len(msgs) - (len(compressed) - 1)])
                    msgs = compressed
                    rewrite = True

        # 3) 仍然超预算 → 逐条丢最旧，但至少保留最近一轮（user + assistant）
        #    （把用户当前这句话丢了比超预算更糟）
        #
        #    有摘要时它固定在索引 0，丢弃位置因此从 1 开始——否则刚花一次模型
        #    调用换来的摘要会被自己这一步立刻扔掉，压缩完全白做。
        protected = 1 if rewrite and msgs and msgs[0].get("role") == "system" else 0
        if max_tokens > 0 and len(msgs) > 2:
            while len(msgs) > 2 and estimate(msgs) > max_tokens:
                dropped.append(msgs.pop(protected))

        # 4) 被丢掉的旧对话是新长期记忆的原料
        if dropped and store is not None and self._settings.extract:
            await self._harvest(int(self._user_id_from_key(session_key)), dropped)

        return HistoryDecision(messages=msgs, rewrite=rewrite)

    async def observe_turn(
        self,
        *,
        session_key: str,
        user_id: int,
        group_id: int,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """一轮对话结束后：提炼事实 + 节流演化"""
        store = self._store
        if store is None or not self._settings.enabled:
            return

        await asyncio.to_thread(store.set_turn, session_key)

        if self._settings.extract and user_id:
            facts = extract_facts(user_text)
            for fact in facts:
                await self._remember(int(user_id), fact)

        await self._maybe_evolve(int(user_id) if user_id else 0)

    async def forget(self, *, session_key: str) -> None:
        """会话被清除：只清该会话的短期痕迹，长期/永久记忆是用户资产不动"""
        self._recalled.pop(session_key, None)
        if self._store is not None:
            await asyncio.to_thread(self._store.forget_session, session_key)

    # ============ 内部：写入与演化 ============

    async def _remember(self, user_id: int, fact: Fact) -> None:
        store = self._store
        if store is None:
            return
        tier = TIER_PERMANENT if fact.permanent else TIER_LONG
        try:
            mem_id, created = await asyncio.to_thread(
                store.upsert,
                scope=SCOPE_USER,
                scope_id=user_id,
                content=fact.content,
                tier=tier,
                importance=fact.importance,
                source="auto",
            )
        except Exception:
            logger.exception(f"写入记忆失败: user_id={user_id}")
            return
        if created:
            logger.debug(f"新增记忆 #{mem_id}: {fact.content}（tier={tier}）")

    async def _harvest(self, user_id: int, dropped: list[dict]) -> None:
        """从被裁掉的旧对话里提炼长期记忆

        只取 user 侧内容：助手的话可能是模型编的，固化进长期记忆会越用越错。
        """
        if not user_id:
            return
        for msg in dropped:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                continue
            for fact in extract_facts(content):
                await self._remember(user_id, fact)

    async def _compress(
        self, session_key: str, msgs: list[dict], summary: SummaryPolicy
    ) -> list[dict] | None:
        """把较早的消息压成一条 system 摘要，保留最近 N 轮原文

        与旧实现的关键差异：调用的是 ``adapter.chat_detail`` 而非 ``chat()``，
        避免经由 LLMManager 主链路又触发一次裁剪/观察（递归）。
        """
        keep = max(2, int(summary.keep_recent_turns) * 2)
        if len(msgs) <= keep + 2:
            return None
        split = len(msgs) - keep
        # 对齐到 user 起始，别把一轮对话切成两半
        while split < len(msgs) - 2 and msgs[split].get("role") != "user":
            split += 1
        old_msgs, recent = msgs[:split], msgs[split:]
        if not old_msgs:
            return None

        lines: list[str] = []
        for m in old_msgs:
            content = m.get("content")
            if not isinstance(content, str):
                content = str(content) if content is not None else ""
            lines.append(f"{m.get('role', 'user')}: {content}")
        transcript = "\n".join(lines)
        prompt = (
            "请将以下对话历史压缩为一段简洁、客观的中文摘要，"
            "保留关键事实、结论与待办事项，不超过 300 字，直接输出摘要内容：\n\n"
            f"{transcript}"
        )

        adapter = getattr(self.llm, "adapter", None) if self.llm is not None else None
        if adapter is None:
            return None
        try:
            result = await adapter.chat_detail(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="你是对话摘要助手，只输出摘要本身，不要任何额外说明。",
                max_tokens=summary.summary_max_tokens,
                temperature=0.3,
            )
        except Exception:
            logger.exception(f"会话摘要失败，退回硬裁剪: key={session_key}")
            return None

        text = (result.content or "").strip()
        if not text:
            return None

        # 摘要用量入库，保持 source=summary 的既有口径（可被 usage 报表区分）
        usage = getattr(result, "usage", None)
        if self.db is not None and usage:
            try:
                await self.db.save_usage(
                    session_key=session_key,
                    user_id=self._user_id_from_key(session_key),
                    model=str(getattr(adapter, "model", "") or ""),
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    source="summary",
                )
            except Exception:
                logger.exception(f"摘要用量入库失败: key={session_key}")

        if self._store is not None:
            await asyncio.to_thread(self._store.set_digest, session_key, text)

        logger.info(
            f"会话历史已摘要压缩: key={session_key}, "
            f"旧消息 {len(old_msgs)} 条 -> 摘要 1 条, 保留最近 {len(recent)} 条"
        )
        return [{"role": "system", "content": f"[对话摘要] {text}"}] + recent

    async def _maybe_evolve(self, user_id: int) -> None:
        """按节流间隔执行衰减 / 晋升 / 归并（默认每小时一次）"""
        store = self._store
        if store is None:
            return
        now = time.time()
        try:
            last = float(await asyncio.to_thread(store.get_meta, "last_evolve", "0") or 0)
        except ValueError:
            last = 0.0
        if now - last < self._settings.evolve_interval_seconds:
            return
        await asyncio.to_thread(store.set_meta, "last_evolve", str(now))

        try:
            decayed, forgotten = await asyncio.to_thread(
                store.decay,
                half_life_days=self._settings.decay_half_life_days,
                floor=self._settings.decay_floor,
            )
            promoted = 0
            if self._settings.auto_promote:
                promoted = await asyncio.to_thread(store.promote)
            merged = 0
            if user_id:
                merged = await asyncio.to_thread(
                    store.consolidate,
                    scope=SCOPE_USER,
                    scope_id=user_id,
                    threshold=self._settings.merge_threshold,
                )
            if decayed or forgotten or promoted or merged:
                logger.info(
                    f"记忆演化: 衰减 {decayed} 条 / 遗忘 {forgotten} 条 / "
                    f"晋升 {promoted} 条 / 归并 {merged} 条"
                )
        except Exception:
            logger.exception("记忆演化失败（不影响对话）")

    # ============ 内部：工具 ============

    @staticmethod
    def _user_id_from_key(session_key: str) -> int:
        try:
            return int(session_key.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            return 0

    def _cap_recalled(self, limit: int = 512) -> None:
        """``_recalled`` 是内存字典：按会话数上限裁剪，防止长期运行无界增长"""
        if len(self._recalled) <= limit:
            return
        for key in list(self._recalled.keys())[: len(self._recalled) - limit]:
            self._recalled.pop(key, None)

    # ============ /memory 命令 ============

    async def _cmd_memory(self, ctx: MatcherContext) -> str:
        """``/memory`` 子命令分发（默认只操作自己的记忆）"""
        store = self._store
        if store is None:
            return "记忆功能尚未就绪（插件未完成加载）。"

        args = (ctx.args or "").strip()
        if not args:
            return await self._memory_overview(ctx, store)

        parts = args.split(maxsplit=1)
        action = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        uid = int(ctx.user_id or 0)

        if action in ("list", "列表"):
            return await self._cmd_list(store, uid, rest)
        if action in ("add", "添加"):
            return await self._cmd_add(store, uid, rest)
        if action in ("pin", "钉住"):
            return await self._cmd_pin(store, uid, rest)
        if action in ("unpin", "取消钉住"):
            return await self._cmd_unpin(store, uid, rest)
        if action in ("forget", "删除"):
            return await self._cmd_forget(store, uid, rest)
        if action in ("search", "搜索"):
            return await self._cmd_search(store, uid, rest)
        if action in ("stats", "统计"):
            return await self._cmd_stats(store, uid)
        if action in ("clear", "清空"):
            return await self._cmd_clear(store, uid)
        if action == "purge":
            return await self._cmd_purge(ctx, store)
        return (
            "记忆命令：\n"
            "/memory              查看概览\n"
            "/memory list [永久]  列出记忆\n"
            "/memory add <内容>   手动记一条\n"
            "/memory pin <#编号>  升为永久记忆\n"
            "/memory unpin <#编号> 取消永久\n"
            "/memory forget <#编号> 删除一条\n"
            "/memory search <关键词>\n"
            "/memory clear        清空自己的全部记忆"
        )

    async def _memory_overview(self, ctx: MatcherContext, store: MemoryStore) -> str:
        uid = int(ctx.user_id or 0)
        st = await asyncio.to_thread(store.stats, scope=SCOPE_USER, scope_id=uid)
        return (
            f"记忆概览（你的）\n"
            f"- 长期记忆：{st['long']} 条\n"
            f"- 永久记忆：{st['permanent']} 条\n"
            f"- 平均显著度：{st['avg_importance']}\n"
            f"短期记忆即当前会话窗口，随会话自动压缩；发 /memory list 查看明细。"
        )

    @staticmethod
    def _fmt(m: Memory) -> str:
        tag = "永久" if m.is_permanent else "长期"
        return f"#{m.id} [{tag}] {m.content}（显著度 {m.importance:.2f} / 命中 {m.hits}）"

    async def _cmd_list(self, store: MemoryStore, uid: int, rest: str) -> str:
        tier = TIER_PERMANENT if rest.strip() in ("永久", "permanent") else None
        items = await asyncio.to_thread(
            store.list_memories, scope=SCOPE_USER, scope_id=uid, tier=tier, limit=30
        )
        if not items:
            return "你还没有任何记忆。说「记住……」或 /memory add <内容> 就能记下来。"
        return f"共 {len(items)} 条：\n" + "\n".join(self._fmt(m) for m in items)

    async def _cmd_add(self, store: MemoryStore, uid: int, rest: str) -> str:
        if not rest:
            return "格式：/memory add <要记住的内容>"
        mem_id, created = await asyncio.to_thread(
            store.upsert,
            scope=SCOPE_USER,
            scope_id=uid,
            content=rest,
            tier=TIER_LONG,
            importance=1.0,
            source="manual",
        )
        if created:
            return f"已记住 #{mem_id}：{rest}"
        return f"这条我已经记着了（#{mem_id}），已提高它的重要度。"

    async def _cmd_pin(self, store: MemoryStore, uid: int, rest: str) -> str:
        """钉住：编号 → 升为永久；纯文本 → 直接写入一条永久记忆"""
        if not rest:
            return "格式：/memory pin <#编号> 或 /memory pin <要永久记住的内容>"
        mem_id = self._parse_id(rest)
        if mem_id is not None:
            ok = await asyncio.to_thread(store.set_tier, mem_id, TIER_PERMANENT)
            if not ok:
                return f"没找到 #{mem_id}。"
            return f"已把 #{mem_id} 设为永久记忆（不再随时间淡忘）。"
        new_id, created = await asyncio.to_thread(
            store.upsert,
            scope=SCOPE_USER,
            scope_id=uid,
            content=rest,
            tier=TIER_PERMANENT,
            importance=1.0,
            source="manual",
        )
        return f"已永久记住 #{new_id}：{rest}" if created else f"这条已在永久记忆里（#{new_id}）。"

    async def _cmd_unpin(self, store: MemoryStore, uid: int, rest: str) -> str:
        mem_id = self._parse_id(rest)
        if mem_id is None:
            return "格式：/memory unpin <#编号>"
        ok = await asyncio.to_thread(store.set_tier, mem_id, TIER_LONG)
        if not ok:
            return f"没找到 #{mem_id}。"
        return f"已把 #{mem_id} 降为长期记忆（此后会随时间淡忘）。"

    async def _cmd_forget(self, store: MemoryStore, uid: int, rest: str) -> str:
        mem_id = self._parse_id(rest)
        if mem_id is None:
            return "格式：/memory forget <#编号>（编号见 /memory list）"
        n = await asyncio.to_thread(store.delete, [mem_id])
        return f"已忘记 #{mem_id}。" if n else f"没找到 #{mem_id}。"

    async def _cmd_search(self, store: MemoryStore, uid: int, rest: str) -> str:
        if not rest:
            return "格式：/memory search <关键词>"
        items = await asyncio.to_thread(
            store.recall, scope=SCOPE_USER, scope_id=uid, query=rest, limit=10
        )
        # 搜索语义与召回不同：召回会无条件带上永久/高显著度记忆（对话时宁可多带
        # 几条），而用户主动搜索时期望的是"真的匹配"，所以这里按相关性再过滤一遍。
        items = [m for m in items if similarity(rest, m.content) > 0]
        if not items:
            return "没有相关记忆。"
        return f"命中 {len(items)} 条：\n" + "\n".join(self._fmt(m) for m in items)

    async def _cmd_stats(self, store: MemoryStore, uid: int) -> str:
        st = await asyncio.to_thread(store.stats, scope=SCOPE_USER, scope_id=uid)
        return (
            f"长期 {st['long']} 条 / 永久 {st['permanent']} 条 / "
            f"合计 {st['total']} 条，平均显著度 {st['avg_importance']}。"
        )

    async def _cmd_clear(self, store: MemoryStore, uid: int) -> str:
        n = await asyncio.to_thread(store.clear, scope=SCOPE_USER, scope_id=uid)
        return f"已清空你的 {n} 条记忆（含永久记忆）。" if n else "你本来就没有记忆。"

    async def _cmd_purge(self, ctx: MatcherContext, store: MemoryStore) -> str:
        """清空所有人的记忆 —— 破坏性操作，仅超管可用"""
        bot = self.bot
        cfg_bot = getattr(getattr(bot, "config", None), "bot", None)
        admins = set(getattr(cfg_bot, "admin_set", set()) or set())
        if int(ctx.user_id or 0) not in admins:
            return "仅超级管理员可执行 /memory purge。"
        n = await asyncio.to_thread(store.purge_all)
        return f"已清空全部记忆（{n} 条）。"

    @staticmethod
    def _parse_id(text: str) -> int | None:
        """解析 ``#12`` / ``12`` 形式的编号"""
        raw = text.strip().lstrip("#")
        return int(raw) if raw.isdigit() else None


__all__ = ["MemoryPlugin", "MemorySettings"]
