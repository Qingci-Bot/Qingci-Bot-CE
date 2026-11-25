# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""情绪系统：四维状态、确定性信号、向基线回归、注入时序

## 归属决策

情绪放在**记忆插件**而不是 chat 插件，理由见 ``emotion.py`` 顶部：它是跨会话
延续、按用户隔离、随时间衰减的「状态」，与记忆的三层/衰减同构，且需要的两个
接缝（``augment_prompt`` / ``observe_turn``）正好由 ``MemoryProvider`` 垄断。
chat 插件是无状态透传层，加状态会让它承担持久化、衰减与配置。

## 本文件锁住的关键行为

1. 信号方向正确（被夸 ↑、被骂 ↓），且各维夹在合法区间
2. 衰减速度有梯度：耐心（2h）远快于亲近（96h）——关系不该一夜清零
3. 提示词里给的是**可执行指令**（句子更短、不用感叹号），不是「你很生气」
4. **时序**：当前这句话就影响这一条回复（不是下一句）
5. 中性时不注入（每轮都塞「你现在心情如何」是纯噪音）
"""

from __future__ import annotations

from pathlib import Path

from bot.plugin.builtin.memory import MemoryPlugin, MemorySettings, emotion
from bot.plugin.builtin.memory.store import MemoryStore

BASE_TS = 1_700_000_000.0  # 固定时间戳，避免测试受真实时钟/时区影响


def _plugin(tmp_path: Path, settings: MemorySettings | None = None) -> MemoryPlugin:
    p = MemoryPlugin()
    p._store = MemoryStore(tmp_path / "memory.db")
    p._settings = settings or MemorySettings()
    return p


# ──────────────────────────────────────────────────────────────────
# 纯逻辑：信号与衰减
# ──────────────────────────────────────────────────────────────────


def test_praise_lifts_mood_and_affinity():
    before = emotion.EmotionState(updated_at=BASE_TS)
    after = emotion.react(before, "谢谢，太好用了", now=BASE_TS)
    assert after.mood > before.mood
    assert after.affinity > before.affinity
    assert after.turns == before.turns + 1


def test_scold_lowers_mood_and_patience():
    before = emotion.EmotionState(updated_at=BASE_TS)
    after = emotion.react(before, "真笨，答得什么垃圾", now=BASE_TS)
    assert after.mood < before.mood
    assert after.patience < before.patience


def test_neutral_message_does_not_move_state():
    """普通提问不该改变情绪——否则随便聊两句就被"喂"出各种状态"""
    before = emotion.EmotionState(updated_at=BASE_TS)
    after = emotion.react(before, "帮我看下这个函数为什么报错", now=BASE_TS)
    assert after.mood == before.mood
    assert after.patience == before.patience
    # 但要落一次 updated_at，否则衰减没有基准点
    assert after.updated_at == BASE_TS


def test_values_stay_in_range():
    """连续夸奖不能把心情顶到 3.0——注入时又是看数值，越界会让指令失真"""
    state = emotion.EmotionState(updated_at=BASE_TS)
    for i in range(30):
        state = emotion.react(state, "谢谢太棒了哈哈", now=BASE_TS + i)
        assert -1.0 <= state.mood <= 1.0
        assert 0.0 <= state.affinity <= 1.0
        assert 0.0 <= state.patience <= 1.0


def test_patience_recovers_faster_than_affinity():
    """耐心的恢复时间常数要远小于亲近：道歉后耐心很快回来，关系是慢变量"""
    hurt = emotion.EmotionState(
        updated_at=BASE_TS, patience=0.1, affinity=0.9, mood=-0.8, energy=-0.8
    )
    after_10h = emotion.decay(hurt, BASE_TS + 10 * 3600)

    # 10 小时 ≈ 5 个耐心半衰周期 → 基本回到基线 0.7
    assert after_10h.patience > 0.6
    # 10 小时对 96 小时常数的亲近几乎没有影响（0.9 -> 0.82，
    # 若换成耐心的 2 小时常数早已归位）
    assert after_10h.affinity > 0.8


def test_instruct_empty_when_neutral():
    """中性时不注入：每轮都塞一段「你现在心情如何」是纯噪音"""
    assert emotion.instruct(emotion.EmotionState(updated_at=BASE_TS)) == ""


def test_instruct_gives_actionable_directions():
    """要写成「句子更短、不用感叹号」，而不是「你现在很低落」"""
    low = emotion.EmotionState(mood=-0.6, patience=0.1, updated_at=BASE_TS)
    text = emotion.instruct(low)
    assert "句子短" in text or "不用感叹号" in text
    assert "直接给结论" in text
    # 必须带安全兜底：不许因为情绪少给信息
    assert "不改变事实" in text
    # 不能只是形容词堆砌（"你很生气"这类无法执行的描述）
    assert "很生气" not in text


def test_night_message_lowers_energy():
    """0-6 点应偏向疲惫：凌晨被叫起来干活和白天不是一回事"""
    import time as _time

    night = _time.mktime((2026, 11, 25, 3, 0, 0, 0, 0, -1))
    state = emotion.react(emotion.EmotionState(updated_at=night), "在吗", now=night)
    assert state.energy < 0


# ──────────────────────────────────────────────────────────────────
# 插件接线：注入时序与开关
# ──────────────────────────────────────────────────────────────────


async def test_emotion_injected_even_without_any_memory(tmp_path):
    """回归：augment_prompt 曾在「召回为空」时提前返回，情绪永远注入不了

    新用户没有任何记忆，但第一句话就骂人——此时仍然要影响这一条回复。
    """
    plugin = _plugin(tmp_path)
    assert not plugin._store.list_memories(scope="user", scope_id=1)

    prompt = await plugin.augment_prompt(
        session_key="private:1",
        user_id=1,
        group_id=0,
        query="真笨，什么都答不对",
        system_prompt="P",
    )
    assert prompt != "P"
    assert "[情绪]" in prompt
    # 一次指责把心情拉到 -0.32（越过阈值），但耐心仍剩 0.52——只该收敛语气，
    # 不该判成「不耐烦」。两条线的区分本身就是要保住的行为。
    assert "句子短" in prompt
    assert "直接给结论" not in prompt


async def test_emotion_affects_the_same_turn(tmp_path):
    """时序：当前这句话必须影响这一条回复，而不是下一句

    若把情绪更新放到 observe_turn（回复之后），被骂的那句会得到乐呵呵的回答。
    """
    plugin = _plugin(tmp_path)
    await plugin.augment_prompt(
        session_key="private:1", user_id=1, group_id=0, query="你好", system_prompt="P"
    )
    # 第一句「你好」不应注入（中性）
    # 第二句骂人，同一条 prompt 里就要体现
    prompt = await plugin.augment_prompt(
        session_key="private:1", user_id=1, group_id=0, query="你真笨", system_prompt="P"
    )
    assert "情绪偏低" in prompt


async def test_emotion_disabled_stops_injection(tmp_path):
    """plugins.memory.emotion.enabled=false 时既不注入也不更新"""
    plugin = _plugin(tmp_path, MemorySettings(emotion=False))
    prompt = await plugin.augment_prompt(
        session_key="private:1", user_id=1, group_id=0, query="你真笨", system_prompt="P"
    )
    assert prompt == "P"
    assert plugin._store.get_emotion(scope_id=1) is None


async def test_emotion_is_isolated_per_user(tmp_path):
    """对 A 的情绪不能施加到 B 身上：这是关系，不是全局气氛"""
    plugin = _plugin(tmp_path)
    await plugin.augment_prompt(
        session_key="private:1", user_id=1, group_id=0, query="真笨", system_prompt="P"
    )
    prompt_b = await plugin.augment_prompt(
        session_key="private:2", user_id=2, group_id=0, query="你好", system_prompt="P"
    )
    assert prompt_b == "P"


async def test_failure_in_emotion_does_not_break_prompt(tmp_path):
    """情绪读写出错时必须原样返回：不能因为情绪把对话打挂"""

    class _BrokenStore(MemoryStore):
        def get_emotion(self, **_kwargs):
            raise RuntimeError("db corrupted")

    plugin = _plugin(tmp_path)
    plugin._store = _BrokenStore(tmp_path / "broken.db")
    prompt = await plugin.augment_prompt(
        session_key="private:1", user_id=1, group_id=0, query="你真笨", system_prompt="P"
    )
    assert prompt == "P"


# ──────────────────────────────────────────────────────────────────
# 命令
# ──────────────────────────────────────────────────────────────────


async def test_mood_command_shows_four_dimensions(tmp_path):
    from types import SimpleNamespace

    plugin = _plugin(tmp_path)
    plugin._store.save_emotion(
        scope_id=100,
        row=emotion.to_row(emotion.EmotionState(mood=-0.5, patience=0.2, updated_at=BASE_TS)),
    )
    text = await plugin._cmd_memory(SimpleNamespace(args="mood", user_id=100))
    for label in ("心情", "精神", "亲近", "耐心"):
        assert label in text
    # 必须说明「只影响语气」，否则用户会以为回答质量被情绪左右
    assert "不影响回答内容" in text


async def test_clear_also_resets_emotion(tmp_path):
    """「清空关于我的一切」应连情绪一起清，否则用户觉得没删干净"""
    from types import SimpleNamespace

    plugin = _plugin(tmp_path)
    plugin._store.upsert(scope="user", scope_id=100, content="对方喜欢猫", importance=0.6)
    plugin._store.save_emotion(
        scope_id=100, row=emotion.to_row(emotion.EmotionState(mood=0.8, updated_at=BASE_TS))
    )

    text = await plugin._cmd_memory(SimpleNamespace(args="clear", user_id=100))
    assert "情绪" in text
    assert plugin._store.get_emotion(scope_id=100) is None


async def test_settings_parse_emotion_section():
    """嵌套节解析：类型不对时退回默认值，不能把整节配置带崩"""
    assert MemorySettings.from_config({}).emotion is True
    assert MemorySettings.from_config({"emotion": {"enabled": False}}).emotion is False
    # 非 dict 的异常写法 → 保持默认
    assert MemorySettings.from_config({"emotion": "off"}).emotion is True
