# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""情绪状态 —— 四个可解释维度 + 确定性信号 + 向基线回归

## 为什么归属记忆插件，而不是对话插件

情绪是**状态**而非**流程**：跨会话延续、随时间衰减、按用户隔离。这三件事与
记忆插件的三层/衰减机制同构，而且它需要的能力恰好是 ``MemoryProvider`` 已经
垄断的两个接缝——``augment_prompt``（影响提示词）与 ``observe_turn``（观察
每轮对话）。放进 chat 插件等于在无状态透传层上再开一套持久化、衰减与配置，
既重复又与职责相悖。

边界刻意划清：情绪**不是记忆**——不进 ``/memory list``、不进星图、不参与召回，
落在独立的 ``emotions`` 表里。

## 为什么不直接让人设"表演情绪"

情绪必须落成**具体可执行的行为指令**（句子更短、不用感叹号、不主动展开话题），
而不是「你现在很生气」这种无法验证的描述——后者只会让模型演得更夸张，同时
损害信息量。所有指令都附带「不改变事实与信息量」的硬约束。

## 维度选择

不用 PAD（pleasure-arousal-dominance）三维模型：它难以映射到具体的语气指令。
这里选四个**各自对应可观察输入、且能翻译成行为**的维度：

- ``mood`` 心情（-1 低落 .. +1 愉快）：被夸/被骂直接驱动
- ``energy`` 精神（-1 疲惫 .. +1 有劲）：深夜、长消息驱动
- ``affinity`` 亲近（0 生疏 .. 1 亲昵）：长期互动累积，衰减极慢（关系不会一夜清零）
- ``patience`` 耐心（0 不耐烦 .. 1 有耐心）：反复追问、被指责驱动，恢复最快

## 时序

情绪在 ``augment_prompt`` 里**先反应、后注入**：用户这句话本身就要影响**这一条**
回复的语气。若放到 ``observe_turn``（回复之后才执行），被骂的那一句仍然会得到
一个乐呵呵的回答，下一句才变——那是明显的时序错误。
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, replace

# 基线：情绪向这里回归，而不是回归到 0。
# affinity 的基线不是 0 而是 0.1——「素不相识」也该有一点基本礼貌；
# patience 基线偏高，因为默认状态应该是肯解释的，不耐烦是要有理由的。
BASELINE_MOOD = 0.0
BASELINE_ENERGY = 0.0
BASELINE_AFFINITY = 0.1
BASELINE_PATIENCE = 0.7

# 各维度的回归时间常数（小时）：越小的恢复越快
TAU_MOOD = 5.0
TAU_ENERGY = 8.0
TAU_PATIENCE = 2.0
TAU_AFFINITY = 96.0

# 偏离基线超过该幅度时才注入提示词。
# 每轮都塞一段"你现在心情如何"是纯噪音，还会让模型过度关注自身状态。
INJECT_THRESHOLD = 0.25


@dataclass(frozen=True, slots=True)
class EmotionState:
    """某一时刻的情绪（不可变，变换都返回新实例）"""

    mood: float = BASELINE_MOOD
    energy: float = BASELINE_ENERGY
    affinity: float = BASELINE_AFFINITY
    patience: float = BASELINE_PATIENCE
    updated_at: float = 0.0
    turns: int = 0

    @property
    def is_neutral(self) -> bool:
        """是否与基线无实质差异（此时不注入提示词）"""
        return not any(
            abs(v) > INJECT_THRESHOLD
            for v in (
                self.mood - BASELINE_MOOD,
                self.energy - BASELINE_ENERGY,
                self.affinity - BASELINE_AFFINITY,
                self.patience - BASELINE_PATIENCE,
            )
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _decay_one(value: float, base: float, elapsed_hours: float, tau: float) -> float:
    """指数回归基线：elapsed 越久越接近 base"""
    if tau <= 0:
        return base
    factor = math.exp(-max(0.0, elapsed_hours) / tau)
    return base + (value - base) * factor


def decay(state: EmotionState, now: float) -> EmotionState:
    """按距上次更新的时长向基线回归（惰性衰减，不需要定时任务）"""
    elapsed = (now - state.updated_at) / 3600.0 if state.updated_at else 0.0
    if elapsed <= 0:
        return state
    return replace(
        state,
        mood=_decay_one(state.mood, BASELINE_MOOD, elapsed, TAU_MOOD),
        energy=_decay_one(state.energy, BASELINE_ENERGY, elapsed, TAU_ENERGY),
        affinity=_decay_one(state.affinity, BASELINE_AFFINITY, elapsed, TAU_AFFINITY),
        patience=_decay_one(state.patience, BASELINE_PATIENCE, elapsed, TAU_PATIENCE),
        updated_at=now,
    )


@dataclass(frozen=True, slots=True)
class _Signal:
    """一条确定性信号 → 情绪增量"""

    name: str
    pattern: re.Pattern[str]
    mood: float = 0.0
    energy: float = 0.0
    affinity: float = 0.0
    patience: float = 0.0


# 规则刻意保守：宁可少变，也不要把「谢谢」当成表白、把一句玩笑当成攻击。
# 全部是确定性的（不调模型）：零成本、可复现、可单测，与事实提炼同一取舍。
_SIGNALS: tuple[_Signal, ...] = (
    _Signal(
        "praise",
        re.compile(r"谢谢|感谢|辛苦了|厉害|太棒|很棒|好用|牛啊|牛逼|爱了|喜欢你|靠谱"),
        mood=0.12,
        affinity=0.07,
        patience=0.04,
    ),
    _Signal(
        "scold",
        # 「滚」单字容易误伤（"滚动的日志"），要求是独立成词或跟语气词
        re.compile(r"笨|蠢|垃圾|废物|没用|闭嘴|滚(?:开|吧|蛋|啊)|傻|智障|废物点心"),
        # 幅度取 -0.32：必须一次就跨过 INJECT_THRESHOLD(0.25)，
        # 否则「刚被指责的这一句」仍会得到轻快的回答，下一句才收敛——
        # 那是明显的时序错误。回落到阈值以内约需 1.2 小时（tau=5h），
        # 与「气一会儿就过去」的直觉一致。
        mood=-0.32,
        patience=-0.18,
    ),
    _Signal(
        "confusion",
        re.compile(r"没听懂|听不懂|没看懂|再说一遍|重说|答非所问|不对吧|不是这个意思"),
        energy=-0.05,
        patience=-0.10,
    ),
    _Signal(
        "warmth",
        re.compile(r"哈哈|嘿嘿|抱抱|贴贴|摸摸|晚安|早安|早上好|辛苦了"),
        mood=0.06,
        affinity=0.06,
    ),
    _Signal(
        "insist",
        # 连续的催促会消耗耐心，但幅度要小：用户着急不是对 Bot 有意见
        re.compile(r"快点|怎么还不|到底|赶紧|你行不行|又错了"),
        energy=-0.04,
        patience=-0.06,
    ),
)

_LONG_MESSAGE_CHARS = 180  # 超过这个长度视为「读起来费劲」，轻微消耗精力


def react(state: EmotionState, text: str, now: float | None = None) -> EmotionState:
    """根据用户这一条消息更新情绪（先衰减再叠加信号）"""
    now = time.time() if now is None else now
    text = text or ""
    # updated_at 必须每次都落到当前时刻：否则首次调用（updated_at=0）之后
    # 下一次仍以 0 为基准算时长，衰减永远不会发生
    next_state = replace(decay(state, now), updated_at=now)

    mood = next_state.mood
    energy = next_state.energy
    affinity = next_state.affinity
    patience = next_state.patience

    matched = False
    for signal in _SIGNALS:
        if signal.pattern.search(text):
            matched = True
            mood += signal.mood
            energy += signal.energy
            affinity += signal.affinity
            patience += signal.patience

    if len(text) >= _LONG_MESSAGE_CHARS:
        energy -= 0.04
        matched = True

    # 深夜（0-6 点）向「疲惫」靠拢：每次只挪一小步，避免 03:00 一条消息就
    # 把状态打到谷底；白天不反向拉回，交给正常衰减
    hour = time.localtime(now).tm_hour
    if 0 <= hour < 6:
        energy += (-0.4 - energy) * 0.2
        matched = True

    if not matched:
        # 没有信号也要落一次 updated_at，让衰减有基准点
        return next_state

    return replace(
        next_state,
        mood=_clamp(mood, -1.0, 1.0),
        energy=_clamp(energy, -1.0, 1.0),
        affinity=_clamp(affinity, 0.0, 1.0),
        patience=_clamp(patience, 0.0, 1.0),
        turns=next_state.turns + 1,
    )


def describe(state: EmotionState) -> str:
    """一句话描述当前情绪（供 `/memory mood` 显示）"""
    parts: list[str] = []
    if state.mood >= 0.35:
        parts.append("心情不错")
    elif state.mood <= -0.3:
        parts.append("情绪有点低")
    else:
        parts.append("心情平稳")

    if state.energy <= -0.35:
        parts.append("有点累")
    elif state.energy >= 0.35:
        parts.append("精神很足")

    if state.patience <= 0.3:
        parts.append("耐心不多了")
    if state.affinity >= 0.55:
        parts.append("和你挺亲近")
    elif state.affinity <= 0.05:
        parts.append("还很生疏")

    return "、".join(parts)


_INJECT_HEADER = "\n\n[情绪] 你此刻的状态（由近期互动累积，只影响语气）：\n"
_SAFETY = "以上只调整语气与详略，不改变事实、不省略该给的信息；不确定就直说不确定。"


def instruct(state: EmotionState) -> str:
    """把情绪翻译成可执行的行为指令；接近中性时返回空串（不注入）

    刻意写成「做什么/不做什么」而不是形容词：模型对「句子更短、不用感叹号」
    的执行力，远高于对「你现在很低落」的演绎——后者容易演成夸张的委屈。
    """
    if state.is_neutral:
        return ""

    lines: list[str] = []
    if state.mood <= -0.3:
        lines.append("· 情绪偏低：句子短一些，不用感叹号，不主动开启新话题；但问题要照实回答。")
    elif state.mood >= 0.45 and state.energy >= 0.2:
        lines.append("· 心情不错：语气可以轻快，最多用一个口语化的玩笑，别堆表情。")

    if state.energy <= -0.4:
        lines.append("· 有点疲惫：尽量一句话说完，避免长分点与铺陈。")
    elif state.energy >= 0.5:
        lines.append("· 精神很足：可以多给一点细节或一个可选建议。")

    if state.patience <= 0.3:
        lines.append("· 耐心不足：直接给结论，不复述已说过的内容，不解释背景。")

    if state.affinity >= 0.55:
        lines.append("· 关系亲近：可以用更随意的口气，允许省略客套。")
    elif state.affinity <= 0.02:
        lines.append("· 还很生疏：保持礼貌，不套近乎、不用昵称。")

    if not lines:
        return ""
    return _INJECT_HEADER + "\n".join(lines) + "\n" + _SAFETY


def to_row(state: EmotionState) -> tuple[float, float, float, float, float, int]:
    """转为 SQLite 行（与 store 的列顺序一致）"""
    return (
        state.mood,
        state.energy,
        state.affinity,
        state.patience,
        state.updated_at,
        state.turns,
    )


def from_row(row: tuple[float, float, float, float, float, int]) -> EmotionState:
    mood, energy, affinity, patience, updated_at, turns = row
    return EmotionState(
        mood=float(mood),
        energy=float(energy),
        affinity=float(affinity),
        patience=float(patience),
        updated_at=float(updated_at),
        turns=int(turns),
    )
