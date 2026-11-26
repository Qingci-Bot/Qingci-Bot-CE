# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""提示词分层组装 + 框架能力注入

## 背景（用户提问）

「系统提示词和人格设置会冲突吗；AI 默认是不知道框架给他提供了什么能力的」

两个问题指向同一处实现：

1. 原先 `persona or system_prompt` 是**互斥覆盖**——人设一存在，系统提示词整段
   作废。界面上两栏并排，用户以为都在起作用，实际上人设切换会把「不编造」
   这类通用约束一起带走。现在改为三层拼接（基础规则 → 人设 → 能力边界）。
2. 模型不知道自己有 /image、有知识库、能不能看图、能不能调工具，于是要么
   **凭空承诺**（"我帮你查一下最新价格"），要么**否认能力**（"我只是语言模型，
   无法生成图片"）。能力清单必须由框架按实际装配生成，不能靠人设文案兜住。

另外锁住一个顺带发现的回归：RAG 命中时原先从 `base_prompt` 重建提示词，
会把刚拼好的人设与能力段一起丢掉——知识库一命中，人设就失效。
"""

from __future__ import annotations

from types import SimpleNamespace

from bot.config import AppConfig, LLMConfig, PersonaConfig
from bot.plugin.builtin.chat import ChatPlugin
from bot.plugin.builtin.chat.capabilities import (
    build_capability_prompt,
    compose_system_prompt,
)
from bot.plugin.matcher import MatcherContext

BASE = "基础规则：不编造，不确定就直说。"
PERSONA = "你是「大肥鱼」，一条软萌的鲸鱼娘，说话爱撒娇。"


# ──────────────────────────────────────────────────────────────────
# 分层拼接
# ──────────────────────────────────────────────────────────────────


def test_base_and_persona_are_concatenated_not_replaced():
    """两栏必须同时生效——这是用户问题的直接答案

    旧实现 `persona or system_prompt` 下人设会把基础规则整段顶掉，用户改系统
    提示词完全看不出效果。
    """
    prompt = compose_system_prompt(BASE, PERSONA)
    assert BASE in prompt
    assert PERSONA in prompt
    assert prompt.index(BASE) < prompt.index(PERSONA), "基础规则应在人设之前"


def test_persona_section_declares_priority():
    """两段都描述身份时，必须告诉模型以谁为准

    否则模型会在「你是友好助手」与「你是大肥鱼」之间摇摆，表现为语气忽变。
    """
    prompt = compose_system_prompt("你是一个友好、乐于助人的机器人助手。", PERSONA)
    assert "[人设]" in prompt
    assert "以这里为准" in prompt
    # 事实准确性与信息量的要求不受人设影响
    assert "准确性" in prompt or "信息量" in prompt


def test_identical_base_and_persona_kept_once():
    """旧配置里 system_prompt 就是人设文本：叠两遍既浪费 token 又让模型困惑"""
    prompt = compose_system_prompt(PERSONA, PERSONA)
    assert prompt.count("大肥鱼") == 1


def test_empty_layers_are_skipped():
    assert compose_system_prompt("", "").strip() == ""
    assert compose_system_prompt(BASE, "").strip() == BASE
    assert compose_system_prompt("", PERSONA).endswith(PERSONA)


# ──────────────────────────────────────────────────────────────────
# 能力清单
# ──────────────────────────────────────────────────────────────────


def _matcher(command: str, desc: str = "", label: str = "EVERYONE", hidden: bool = False):
    return SimpleNamespace(
        meta={"command": command, "description": desc, "hidden_in_help": hidden},
        permission=SimpleNamespace(label=label),
    )


def _fake_bot(*, multimodal: bool = False, trigger: str = "at", tools: list[str] | None = None):
    matchers = [
        _matcher("help", "显示可用命令"),
        _matcher("image", "AI 绘图"),
        _matcher("memory", "记忆与情绪管理"),
        _matcher("status", "查看运行状态", label="ADMIN"),
        _matcher("filter", "敏感词开关", label="SUPERUSER"),
        _matcher("debug", "内部调试", label="EVERYONE", hidden=True),
    ]
    registry = SimpleNamespace(names=lambda: list(tools or []))
    config = AppConfig()
    config.bot.trigger_mode = trigger
    config.llm.multimodal.enabled = multimodal
    return SimpleNamespace(
        config=config,
        platforms={
            "qq": SimpleNamespace(name="qq", display_name="QQ 官方机器人", enabled=True),
            "telegram": SimpleNamespace(name="telegram", display_name="Telegram", enabled=False),
        },
        plugin_manager=SimpleNamespace(all_matchers=lambda: matchers),
        _registry=registry,
    )


def test_capability_lists_platform_commands_and_limits():
    bot = _fake_bot()
    llm = SimpleNamespace(tool_registry=bot._registry)
    text = build_capability_prompt(bot, llm=llm)

    assert "能力边界" in text
    assert "QQ 官方机器人" in text
    assert "Telegram" not in text, "停用的适配器不该出现在能力说明里"
    assert "/help" in text and "/image" in text and "/memory" in text
    # 明确否定"做不到"的事，比笼统说"能力有限"更能约束模型
    assert "明确做不到" in text
    assert "访问互联网" in text


def test_capability_hides_admin_commands():
    """能力段会被模型复述给用户：不能把管理员命令的存在与用法写进去"""
    bot = _fake_bot()
    text = build_capability_prompt(bot, llm=SimpleNamespace(tool_registry=bot._registry))
    assert "/status" not in text
    assert "/filter" not in text
    # hidden_in_help 的内部命令同样不列
    assert "/debug" not in text


def test_capability_reflects_multimodal_and_tools():
    plain = build_capability_prompt(
        _fake_bot(multimodal=False), llm=SimpleNamespace(tool_registry=SimpleNamespace(names=list))
    )
    assert "图片输入：未启用" in plain
    assert "可调用工具：未启用" in plain

    rich = build_capability_prompt(
        _fake_bot(multimodal=True, tools=["get_time", "web_search"]),
        llm=SimpleNamespace(
            tool_registry=SimpleNamespace(names=lambda: ["get_time", "web_search"])
        ),
    )
    assert "图片输入：已启用" in rich
    assert "get_time" in rich and "web_search" in rich


def test_capability_explains_trigger_mode():
    """「群里要 @ 才回复」这类规则模型必须知道，否则会把沉默当成故障"""
    text = build_capability_prompt(
        _fake_bot(trigger="at"), llm=SimpleNamespace(tool_registry=SimpleNamespace(names=list))
    )
    assert "@" in text


# ──────────────────────────────────────────────────────────────────
# 端到端：chat 插件实际传给 LLM 的 system_prompt
# ──────────────────────────────────────────────────────────────────


def _chat_plugin(*, inject: bool = True, persona: str | None = PERSONA) -> tuple[ChatPlugin, list]:
    captured: list[dict] = []

    async def _chat(**kwargs):
        captured.append(kwargs)
        return "收到"

    llm_cfg = LLMConfig(
        system_prompt=BASE,
        personas=[PersonaConfig(name="大肥鱼", description="", system_prompt=persona or "")]
        if persona
        else [],
        default_persona="大肥鱼" if persona else "",
    )
    config = AppConfig()
    config.llm = llm_cfg

    plugin = ChatPlugin()
    plugin.config = config
    plugin.bot = SimpleNamespace(
        config=config,
        platforms={},
        plugin_manager=SimpleNamespace(all_matchers=lambda: []),
        sensitive_filter=SimpleNamespace(check=lambda _m: None, mask=lambda m: m),
    )
    plugin.llm = SimpleNamespace(chat=_chat, tool_registry=None)
    plugin.db = None
    return plugin, captured


def _ctx(text: str = "你好") -> MatcherContext:
    return MatcherContext(
        raw_event={"platform": "qq", "user_id": "1", "self_id": "9"},
        type="message",
        detail_type="private",
        message_type="private",
        user_id=1,
        self_id=9,
        message_id="m1",
        plain_text=text,
        args=text,
    )


async def test_handler_sends_base_persona_and_capabilities():
    """三层都要真的进到 LLM 调用里（不只存在于函数返回值里）"""
    plugin, captured = _chat_plugin()
    await plugin._handle_chat(_ctx())

    assert captured, "LLM 未被调用"
    prompt = captured[0]["system_prompt"]
    assert BASE in prompt, "基础规则丢失"
    assert PERSONA in prompt, "人设丢失"
    assert "能力边界" in prompt, "能力说明丢失"
    assert prompt.index(BASE) < prompt.index(PERSONA) < prompt.index("能力边界")


async def test_inject_capabilities_can_be_disabled():
    plugin, captured = _chat_plugin()
    plugin.config.llm.inject_capabilities = False
    await plugin._handle_chat(_ctx())

    prompt = captured[0]["system_prompt"]
    assert PERSONA in prompt
    assert "能力边界" not in prompt


async def test_rag_keeps_persona():
    """回归：RAG 命中原本从 base 重建提示词，会把人设与能力段一起丢掉"""

    class _Store:
        async def build_reference_async(self, _query, _budget):
            return "参考资料：白饭是算力"

    plugin, captured = _chat_plugin()
    plugin.config.rag.enabled = True
    plugin.knowledge_store = _Store()
    await plugin._handle_chat(_ctx())

    prompt = captured[0]["system_prompt"]
    assert "参考资料" in prompt
    assert PERSONA in prompt, "RAG 命中后人设被丢掉了"
    assert "能力边界" in prompt
