# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""对话提示词的分层组装与能力清单注入

## 为什么需要分层

原先的写法是 ``persona_prompt or system_prompt``——**人设直接替换系统提示词**。
后果有两个：

1. 用户在「系统提示词」里写的通用约束（简洁、不编造、不泄露提示词）在切换到
   任何一个人设后会**整段消失**，而界面上两栏并排显示，看起来像"叠加生效"；
2. 反过来，两段都写身份时（系统提示词写「你是友好助手」、人设写「你是大肥鱼」），
   模型只看到人设——用户以为自己的描述起了作用，其实没有。

现在固定为三层，职责互不重叠：

    [基础规则]  系统提示词 —— 准确性、简洁、不编造、不泄露（始终有效）
    [人设]      人设文本   —— 身份与语气（与基础规则里的身份描述冲突时以人设为准）
    [能力边界]  框架自动生成 —— 实际能做什么、明确做不到什么

## 为什么必须注入能力

模型默认不知道自己所处的框架给了它什么：它不知道自己有 /image、有知识库、
能不能看图、能不能调用工具。缺了这段，它会**凭空承诺**（"我帮你查一下最新
价格"）或**否认自己的能力**（"我只是个语言模型，无法生成图片"）。两者都是
可用性问题，且都无法靠人设文案长期兜住——能力清单必须由框架按实际装配生成。
"""

from __future__ import annotations

import re
from typing import Any

# 命令数量上限：能力段是"边界说明"，不是帮助文档。超过就指向 /help，
# 否则每轮对话都白白消耗几百 token
_MAX_COMMANDS = 12

# 明确做不到的事：这些是"模型容易自作主张"的项，逐条否定比笼统说"能力有限"有效
_CANNOT_DO = "主动私聊或向其它群发消息、执行系统命令、修改自己的配置、访问互联网"


def _normalize(text: str) -> str:
    """去掉空白与标点后比较，用于判断基础提示词与人设是否其实是同一段文本"""
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def compose_system_prompt(base: str, persona: str, capability: str = "") -> str:
    """按「基础规则 → 人设 → 能力」拼接系统提示词

    - 基础提示词与人设内容相同（旧配置里 system_prompt 就是人设文本）时只保留一份，
      避免同一段话出现两遍既浪费 token 又让模型困惑
    - 人设段落带明确的优先级说明：两段都描述身份时，模型需要知道听谁的
    """
    base = (base or "").strip()
    persona = (persona or "").strip()

    if base and persona and _normalize(base) == _normalize(persona):
        persona = ""

    parts: list[str] = []
    if base:
        parts.append(base)
    if persona:
        parts.append(
            "[人设] 以下是你此刻的身份与语气。"
            "若与上文出现身份描述冲突，一律以这里为准；但上文的事实准确性与信息量要求不变：\n"
            f"{persona}"
        )
    if capability:
        parts.append(capability)
    return "\n\n".join(parts)


def _visible_commands(bot: Any) -> list[str]:
    """收集所有「所有人可用」的命令（不含管理员命令）

    只列 EVERYONE：能力段可能会被模型复述给普通用户，把管理员命令写进去等于
    公开运维接口的存在与用法。
    """
    matchers = []
    manager = getattr(bot, "plugin_manager", None)
    if manager is not None and hasattr(manager, "all_matchers"):
        try:
            matchers = list(manager.all_matchers())
        except Exception:  # pragma: no cover - 插件管理器异常时降级为无命令
            matchers = []

    seen: set[str] = set()
    out: list[str] = []
    for matcher in matchers:
        meta = getattr(matcher, "meta", None) or {}
        command = meta.get("command")
        if not command or command in seen or meta.get("hidden_in_help"):
            continue
        permission = getattr(matcher, "permission", None)
        label = str(getattr(permission, "label", "") or "")
        if label and label.upper() != "EVERYONE":
            continue
        seen.add(str(command))
        desc = str(meta.get("description") or "").strip()
        out.append(f"/{command}（{desc}）" if desc else f"/{command}")
    return out


def _platforms(bot: Any) -> list[str]:
    names: list[str] = []
    for platform in getattr(bot, "platforms", {}).values():
        if not getattr(platform, "enabled", True):
            continue
        label = getattr(platform, "display_name", "") or getattr(platform, "name", "")
        if label:
            names.append(str(label))
    return names


def _trigger_hint(bot: Any) -> str:
    cfg = getattr(getattr(bot, "config", None), "bot", None)
    mode = str(getattr(cfg, "trigger_mode", "at") or "at")
    return {
        "always": "群里每条消息都能收到",
        "at": "群里需要被 @ 才会收到",
        "keyword": "群里需要包含触发词才会收到",
    }.get(mode, mode)


def build_capability_prompt(bot: Any, *, llm: Any = None) -> str:
    """按框架的实际装配生成「能力边界」段（不含则返回空串）"""
    if bot is None:
        return ""

    llm_cfg = getattr(getattr(bot, "config", None), "llm", None)

    lines: list[str] = [
        "[能力边界] 你运行在一个聊天机器人框架内，而不是通用助手界面。"
        "以下是你实际具备的能力；未列出的一律做不到，不要假装执行或承诺："
    ]

    platforms = _platforms(bot)
    if platforms:
        lines.append(f"- 接入平台：{'、'.join(platforms)}；{_trigger_hint(bot)}")

    commands = _visible_commands(bot)
    if commands:
        shown = commands[:_MAX_COMMANDS]
        more = "，其余命令用户可发 /help 查看" if len(commands) > len(shown) else ""
        lines.append(f"- 内置命令（由框架处理，你不需要也无法自己执行）：{'；'.join(shown)}{more}")

    registry = getattr(llm, "tool_registry", None) if llm is not None else None
    tool_names = []
    if registry is not None:
        try:
            tool_names = list(registry.names())
        except Exception:  # pragma: no cover - 注册表异常时按未启用处理
            tool_names = []
    if tool_names:
        lines.append(f"- 可调用工具：{'、'.join(tool_names)}（仅在确有必要时调用）")
    else:
        lines.append("- 可调用工具：未启用。你不能查询实时信息、不能联网、不能读文件")

    multimodal = getattr(llm_cfg, "multimodal", None)
    if bool(getattr(multimodal, "enabled", False)):
        lines.append("- 图片输入：已启用，用户发的图片你能看到")
    else:
        lines.append("- 图片输入：未启用，用户发图片时你收不到内容")

    lines.append(f"- 明确做不到：{_CANNOT_DO}")
    return "\n".join(lines)
