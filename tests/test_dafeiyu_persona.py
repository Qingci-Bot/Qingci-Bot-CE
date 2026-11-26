# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""出厂人设「大肥鱼」的一致性契约

「大肥鱼」是 DeepSeek 那条蓝色鲸鱼 Logo 被自家模型认成「一条蓝色大肥鱼」后，
在网友手里长出来的 Q 版鲸鱼娘（民间二创，非官方看板娘）。人设文本散落在三处：

1. ``bot/config.py`` 的 :data:`DAFEIYU_PROMPT`（权威来源，真正下发给模型）
2. ``config.example.yaml`` 的 ``llm.system_prompt`` / ``llm.personas[0]``（给用户看）
3. ``web/src/views/ChatConfig.vue`` 的 ``DAFEIYU_PROMPT``（「恢复出厂人设」按钮回填）

三处必须逐字一致，否则会出现「界面点恢复、实际存进去的是另一段人格」这种
最难排查的偏差。本测试把三者钉在一起，并额外锁住标志性设定——防止某次
「润色」把人设悄悄改回一个通用助手，而名字还叫大肥鱼。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from bot.config import DAFEIYU_PERSONA, DAFEIYU_PROMPT

ROOT = Path(__file__).resolve().parent.parent
VUE = ROOT / "web" / "src" / "views" / "ChatConfig.vue"
EXAMPLE = ROOT / "config.example.yaml"

# 大肥鱼的标志性设定（来自公开二创与媒体报道）。少任何一个都说明人设被改回
# 通用助手了：白饭碗=算力的隐喻、鲸鱼娘外形、摸鱼、Token 见底示弱。
SIGNATURE_TRAITS = ("白饭", "Token", "鲸", "摸鱼", "女仆")


def _vue_prompt() -> str:
    """从 ChatConfig.vue 里取出 DAFEIYU_PROMPT 的字符串字面量

    源码是若干单引号字面量用 ``+`` 拼接，这里按行取出后逐段求值（只处理
    本项目实际用到的转义：``\\n`` 与 ``\\'``），避免在测试里引入 JS 解析器。
    """
    src = VUE.read_text(encoding="utf-8")
    m = re.search(r"const DAFEIYU_PROMPT =\s*(.*?);\nconst DAFEIYU_PERSONA", src, re.S)
    assert m, "ChatConfig.vue 里找不到 DAFEIYU_PROMPT 定义"
    body = m.group(1)
    chunks = re.findall(r"'((?:[^'\\]|\\.)*)'", body)
    assert chunks, "DAFEIYU_PROMPT 里没有解析到字符串字面量"
    return "".join(c.replace("\\n", "\n").replace("\\'", "'") for c in chunks)


@pytest.fixture(scope="module")
def example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_prompt_keeps_signature_traits():
    """人设必须保留大肥鱼的标志性设定，不能被润色成通用助手"""
    missing = [t for t in SIGNATURE_TRAITS if t not in DAFEIYU_PROMPT]
    assert not missing, f"人设缺少大肥鱼的标志性设定：{missing}"


def test_prompt_forbids_fabrication():
    """卖萌不能牺牲准确性：人设里必须明确「不确定就说不确定」"""
    assert "不编造" in DAFEIYU_PROMPT
    assert "不确定" in DAFEIYU_PROMPT


def test_persona_name_and_prompt_wired():
    """PersonaConfig 的 name/system_prompt/description 三者自洽"""
    assert DAFEIYU_PERSONA.name == "大肥鱼"
    assert DAFEIYU_PERSONA.system_prompt == DAFEIYU_PROMPT
    assert "大肥鱼" in DAFEIYU_PERSONA.description


def test_config_example_matches_backend(example):
    """config.example.yaml 的人设文本与后端逐字一致，且系统提示词不再兼任人设

    1.23.0-rc5 起提示词分三层：``system_prompt`` 是「基础规则」（不含身份），
    身份与语气由 ``personas`` 提供。此前 ``system_prompt`` 直接放人设文本，
    造成两栏内容重复——用户改系统提示词看不出效果（人设覆盖它），切人设时
    又把通用约束一起丢掉。这里同时锁住「人设一致」与「两栏职责分离」。
    """
    from bot.config import DEFAULT_BASE_PROMPT

    llm = example["llm"]
    # 系统提示词 = 基础规则，且不得再是那段人设文本
    assert llm["system_prompt"] == DEFAULT_BASE_PROMPT
    assert llm["system_prompt"] != DAFEIYU_PROMPT, "系统提示词又变回人设文本了"
    # 基础规则必须含「不编造」这类硬约束：它是切人设后唯一保底的一层
    assert "不编造" in llm["system_prompt"]

    first = llm["personas"][0]
    assert first["name"] == DAFEIYU_PERSONA.name
    assert first["system_prompt"] == DAFEIYU_PROMPT
    assert first["description"] == DAFEIYU_PERSONA.description


def test_web_ui_matches_backend():
    """Web UI「恢复出厂人设」回填的文本与后端逐字一致"""
    assert _vue_prompt() == DAFEIYU_PROMPT
