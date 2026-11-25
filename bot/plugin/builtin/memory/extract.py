# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""从用户消息里提炼值得长期记住的事实（确定性规则，不调用模型）

## 为什么先不做「让模型自己判断该记什么」

那是最容易想到的方案，但代价很实在：每轮对话多一次推理调用，贵且慢，
且模型输出不稳定——同一句话两次运行可能给出不同的记忆条目，去重、演化、
可复现测试全都变难。这里先用可枚举的规则覆盖"自我介绍 / 偏好 / 显式要求
记住"这三类高价值事实，行为可预测、零额外成本、可单元测试。

需要更强的抽取能力时，把 ``extract_facts`` 换成模型实现即可——上层契约
（返回 :class:`Fact` 列表）不变，插件其余部分无需改动。

规则刻意保守：宁可少记，也不要把寒暄、疑问、反问句错记成用户属性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 单条事实的长度上限：太长的"事实"往往是整段叙述，记了也没法用
_MAX_FACT_LEN = 60
# 单条消息最多提炼几条，防止一段长文撑进十几条碎片记忆
_MAX_FACTS_PER_MESSAGE = 3

# 显式要求记住 —— 直接进永久层，不参与衰减
# 用字符串拼接而非 % / f-string：正则里的 {2,60} 在 f-string 中要写成
# {{2,60}}，可读性差且容易写漏（ruff UP031 也不允许 % 格式化）。
_EXPLICIT_MAX = "{2," + str(_MAX_FACT_LEN) + "}"
_EXPLICIT = (
    re.compile(r"(?:请|麻烦)?记住[：:，,、\s]*(?P<v>." + _EXPLICIT_MAX + r")"),
    re.compile(r"别忘(?:了|记)?[：:，,、\s]*(?P<v>." + _EXPLICIT_MAX + r")"),
)

# 自称/称呼
_NAME = (
    re.compile(r"(?:以后|之后|请)?叫我(?P<v>[\u4e00-\u9fa5A-Za-z0-9_]{1,12})"),
    re.compile(r"我(?:的名字)?叫(?P<v>[\u4e00-\u9fa5A-Za-z0-9_]{1,12})"),
)

# 偏好（喜欢/讨厌）
_PREFERENCE = (
    re.compile(r"我(?:很|最|特别|超级|挺)?(?:喜欢|爱)(?P<v>[^\s，。！？,.!?]{1,20})"),
    re.compile(r"我(?:很|最|特别|挺)?(?:不喜欢|讨厌|受不了)(?P<v>[^\s，。！？,.!?]{1,20})"),
)

# 「我的 X 是 Y」
_MINE = re.compile(
    r"我的(?P<key>[\u4e00-\u9fa5A-Za-z0-9]{1,10})(?:是|叫)(?P<val>[^\s，。！？,.!?]{1,20})"
)

# 「叫我」后面跟的常常是动词而不是称呼（"叫我看看这个"）。这里用前缀黑名单
# 挡掉高频误判：宁可漏记一个昵称，也不要把动词短语写进"对方希望你称呼其为"。
_NON_NAME_PREFIXES = (
    "看看",
    "一下",
    "一声",
    "知道",
    "说",
    "做",
    "去",
    "来",
    "先",
    "别",
    "不要",
    "记住",
    "想想",
    "试试",
    "瞧瞧",
)


def _plausible_name(name: str) -> bool:
    """判断 ``叫我``/``我叫`` 后面捕获的是否像称呼"""
    if not name or len(name) > 12:
        return False
    return not any(name.startswith(p) for p in _NON_NAME_PREFIXES)


# 称呼末尾的语气/填充词：「叫我阿鱼就行」里的「就行」不是名字的一部分
_NAME_TAIL = re.compile(r"(?:就行|就好|好了|吧|呀|啊|哦|啦|呢|嘛|哈|喔)+$")


def _trim_name(name: str) -> str:
    return _NAME_TAIL.sub("", name).strip()


# 疑问/反问句式不提炼：问句里的「我喜欢吗」不是事实
_QUESTION_HINTS = ("吗", "?", "？", "什么", "怎么", "为什么", "是不是")


@dataclass(frozen=True, slots=True)
class Fact:
    """一条待写入记忆的事实

    Attributes:
        content: 可直接展示、也可直接注入提示词的自然语言陈述
        importance: 初始显著性 0..1
        permanent: 是否直接进永久层（用户明确要求记住）
    """

    content: str
    importance: float
    permanent: bool = False


def _clean(text: str) -> str:
    """去掉首尾标点与空白，压掉内部连续空白"""
    return re.sub(r"\s+", " ", text).strip(" \t\r\n，。！？、；：,.!?;:")


def _looks_like_question(text: str) -> bool:
    return any(h in text for h in _QUESTION_HINTS)


def extract_facts(text: str) -> list[Fact]:
    """从一条用户消息里提炼事实（最多 ``_MAX_FACTS_PER_MESSAGE`` 条）

    只处理用户自己说的话——助手回复里的内容可能是模型编的，不能当作事实
    沉淀（那会把幻觉固化进长期记忆，越用越错）。
    """
    if not text or len(text) > 500:
        # 超长消息多半是粘贴的文章/代码，不是自我介绍
        return []
    if _looks_like_question(text):
        return []

    facts: list[Fact] = []
    seen: set[str] = set()

    def _add(content: str, importance: float, permanent: bool = False) -> None:
        content = _clean(content)
        if not content or content in seen:
            return
        seen.add(content)
        facts.append(Fact(content=content, importance=importance, permanent=permanent))

    # 1) 显式要求记住：优先级最高，内容原样保留
    for pattern in _EXPLICIT:
        for m in pattern.finditer(text):
            _add(m.group("v"), 1.0, permanent=True)

    # 2) 称呼 / 名字
    for pattern in _NAME:
        for m in pattern.finditer(text):
            name = _trim_name(_clean(m.group("v")))
            if _plausible_name(name):
                _add(f"对方希望你称呼其为「{name}」", 0.8)

    # 3) 偏好
    for idx, pattern in enumerate(_PREFERENCE):
        for m in pattern.finditer(text):
            verb = "不喜欢" if idx == 1 else "喜欢"
            _add(f"对方{verb}「{_clean(m.group('v'))}」", 0.6)

    # 4) 我的 X 是 Y
    for m in _MINE.finditer(text):
        key, val = _clean(m.group("key")), _clean(m.group("val"))
        if key and val:
            _add(f"对方的{key}是「{val}」", 0.65)

    return facts[:_MAX_FACTS_PER_MESSAGE]


__all__ = ["Fact", "extract_facts"]
