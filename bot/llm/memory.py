# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""记忆能力接缝 —— 框架只保留协议，具体实现由内置 memory 插件提供

## 为什么要有这一层

1.23 之前，「上下文管理」是直接写在 :class:`~bot.llm.manager.LLMManager` 里的：
按条数硬裁剪、超 token 就调模型做摘要。这让框架替插件做了策略决定，而记忆
本身（分层、显著性、遗忘、跨会话沉淀）属于产品能力，不该固化在传输层。

因此这里只定义两件事：

- :class:`MemoryProvider`：记忆提供方协议，由内置 ``memory`` 插件实现；
- :class:`FallbackMemoryProvider`：未注入提供方时的**兜底**，只做最保守的
  硬裁剪。它不承担记忆功能，存在意义仅有一条——防止上下文无限增长把
  LLM 请求撑爆（没有它，一旦插件加载失败就是线上事故）。

DB 读写仍留在框架侧（``_trim_history`` 统一处理），因为那是
「会话表的一致性」问题，不是记忆策略问题：提供方只回答"应该用哪些消息、
是否需要重写历史"，落库由框架一次做完，避免两处各写一遍导致内存与 DB 分裂。

内置插件不可移除（见 ``bot.plugin._loading._BUILTIN_PLUGINS``），所以生产环境
总有真实提供方；兜底只在插件加载失败或单元测试里直接构造 manager 时生效。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

Message = dict


@dataclass(slots=True)
class SummaryPolicy:
    """摘要压缩策略（由框架从 ``session_summary`` + ``llm.enable_summary`` 组装）

    框架只负责把配置翻译成这个值对象，**是否摘要、如何摘要**由提供方决定。
    """

    enabled: bool
    max_messages: int
    max_tokens: int
    keep_recent_turns: int
    summary_max_tokens: int


@dataclass(slots=True)
class HistoryDecision:
    """提供方对「本次请求应该带哪些历史」的决定

    Attributes:
        messages: 最终用于请求的消息列表（按时间正序）
        rewrite: 是否需要用 ``messages`` **整体重写**会话记录。
            摘要压缩会产生一条新消息并吞掉多条旧消息，条数对不上，必须重写；
            单纯的按条裁剪只需截断旧记录，走 ``rewrite=False`` 更省 IO。
    """

    messages: list[Message] = field(default_factory=list)
    rewrite: bool = False


@runtime_checkable
class MemoryProvider(Protocol):
    """记忆提供方协议（内置 ``memory`` 插件实现）

    四个方法对应记忆能力在对话链路上的四个接触点。任一方法抛异常都由框架
    捕获并降级，绝不允许记忆功能把正常对话打挂。
    """

    async def augment_prompt(
        self,
        *,
        session_key: str,
        user_id: int,
        group_id: int,
        query: str,
        system_prompt: str,
    ) -> str:
        """在调用模型前增强系统提示词（注入召回的记忆）

        返回值为最终 system prompt；实现方应在无内容可注入时**原样返回**
        ``system_prompt``，不要把空字符串当成"清空提示词"。
        """
        ...

    async def manage_history(
        self,
        *,
        session_key: str,
        messages: list[Message],
        max_messages: int,
        max_tokens: int,
        summary: SummaryPolicy,
        estimate: Callable[[list[Message]], int],
    ) -> HistoryDecision:
        """决定本次请求携带的历史（裁剪 / 摘要 / 提炼长期记忆）

        Args:
            messages: 当前会话完整历史（含刚追加的用户消息，按时间正序）
            max_messages: 条数上限（``max_history * 2``）
            max_tokens: token 上限（``max_context_tokens``，<=0 表示不限）
            summary: 摘要策略；``enabled`` 为假时不应调用模型做摘要
            estimate: token 估算函数，避免实现方重复实现计数逻辑
        """
        ...

    async def observe_turn(
        self,
        *,
        session_key: str,
        user_id: int,
        group_id: int,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """一轮对话完成后的观察（沉淀记忆、强化显著性、演化）"""
        ...

    async def forget(self, *, session_key: str) -> None:
        """会话被清除时同步清理其短期记忆（长期/永久记忆不受影响）"""
        ...


class FallbackMemoryProvider:
    """兜底提供方：只做硬裁剪，不摘要、不记忆

    保守到"只会变短不会变错"：先按条数截断，再按 token 逐条丢弃最旧消息，
    并且**至少保留最近一轮**——把用户最后那句话丢了比超出预算更糟。
    """

    name = "fallback"

    async def augment_prompt(
        self,
        *,
        session_key: str,
        user_id: int,
        group_id: int,
        query: str,
        system_prompt: str,
    ) -> str:
        return system_prompt

    async def manage_history(
        self,
        *,
        session_key: str,
        messages: list[Message],
        max_messages: int,
        max_tokens: int,
        summary: SummaryPolicy,
        estimate: Callable[[list[Message]], int],
    ) -> HistoryDecision:
        if not messages:
            return HistoryDecision(messages=[])
        trimmed = list(messages)
        if max_messages > 0 and len(trimmed) > max_messages:
            trimmed = trimmed[-max_messages:]
        if max_tokens > 0 and len(trimmed) > 2:
            while len(trimmed) > 2 and estimate(trimmed) > max_tokens:
                trimmed.pop(0)
        # 条数变了就走截断（rewrite=False），框架只需 trim DB 无需整体重写
        return HistoryDecision(messages=trimmed, rewrite=False)

    async def observe_turn(
        self,
        *,
        session_key: str,
        user_id: int,
        group_id: int,
        user_text: str,
        assistant_text: str,
    ) -> None:
        return None

    async def forget(self, *, session_key: str) -> None:
        return None


__all__ = [
    "FallbackMemoryProvider",
    "HistoryDecision",
    "MemoryProvider",
    "Message",
    "SummaryPolicy",
]
