# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""记忆插件测试 —— 三层记忆、自主演化、上下文接管、命令

本文件同时承接了原先挂在 ``LLMManager._summarize_history`` 上的摘要测试：
该能力已从框架迁到插件（框架只留 ``set_memory_provider`` 接缝），断言随
能力一起搬家，覆盖不降低。
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.llm.memory import SummaryPolicy
from bot.plugin.builtin.memory import MemoryPlugin, MemorySettings
from bot.plugin.builtin.memory.extract import extract_facts
from bot.plugin.builtin.memory.store import (
    SCOPE_USER,
    TIER_LONG,
    TIER_PERMANENT,
    MemoryStore,
    normalize,
    similarity,
)

DAY = 86400.0


# ──────────────────────────────────────────────────────────────────
# 测试替身
# ──────────────────────────────────────────────────────────────────


class FakeAdapter:
    """可控摘要输出的假适配器（插件只用到 chat_detail 与 model）"""

    def __init__(self, reply: str = "这是摘要", usage: dict | None = None, fail: bool = False):
        self.model = "fake-model"
        self.reply = reply
        self.usage = usage
        self.fail = fail
        self.calls: list[dict] = []

    async def chat_detail(self, messages, system_prompt=None, max_tokens=2048, temperature=0.7):
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
        if self.fail:
            raise RuntimeError("adapter boom")
        return SimpleNamespace(content=self.reply, usage=self.usage)


class FakeDB:
    async def save_usage(self, **kwargs):
        self.usage = getattr(self, "usage", [])
        self.usage.append(kwargs)


def make_plugin(tmp_path, settings: MemorySettings | None = None) -> MemoryPlugin:
    p = MemoryPlugin()
    p._store = MemoryStore(tmp_path / "memory.db")
    p._settings = settings or MemorySettings()
    return p


def ctx(args: str, user_id: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        args=args,
        user_id=user_id,
        message_type="private",
        group_id=0,
    )


# ──────────────────────────────────────────────────────────────────
# Store：写入 / 去重 / 分层
# ──────────────────────────────────────────────────────────────────


def test_upsert_dedupes_and_reinforces(tmp_path):
    """同一件事重复写入应强化已有条目，而不是长出两条"""
    store = MemoryStore(tmp_path / "m.db")
    id1, created1 = store.upsert(scope=SCOPE_USER, scope_id=1, content="对方喜欢猫", importance=0.5)
    id2, created2 = store.upsert(
        scope=SCOPE_USER, scope_id=1, content="对方喜欢猫！", importance=0.5
    )
    assert created1 is True
    assert created2 is False
    assert id1 == id2  # 归一化后同键（去掉标点）
    mem = store.get(id1)
    assert mem is not None
    assert mem.hits == 2
    assert mem.importance > 0.5
    assert len(store.list_memories(scope=SCOPE_USER, scope_id=1)) == 1


def test_upsert_upgrades_tier_to_permanent(tmp_path):
    """已存在条目被永久写入时应升层，且不会被之后的长期写入降回来"""
    store = MemoryStore(tmp_path / "m.db")
    mem_id, _ = store.upsert(scope=SCOPE_USER, scope_id=1, content="他叫小明", tier=TIER_LONG)
    store.upsert(scope=SCOPE_USER, scope_id=1, content="他叫小明", tier=TIER_PERMANENT)
    assert store.get(mem_id).tier == TIER_PERMANENT
    store.upsert(scope=SCOPE_USER, scope_id=1, content="他叫小明", tier=TIER_LONG)
    assert store.get(mem_id).tier == TIER_PERMANENT


def test_memory_is_scoped_per_user(tmp_path):
    """记忆按用户隔离：A 的记忆不能被 B 召回"""
    store = MemoryStore(tmp_path / "m.db")
    store.upsert(scope=SCOPE_USER, scope_id=1, content="A 喜欢猫")
    store.upsert(scope=SCOPE_USER, scope_id=2, content="B 喜欢狗")
    assert [m.content for m in store.list_memories(scope=SCOPE_USER, scope_id=1)] == ["A 喜欢猫"]
    assert [m.content for m in store.list_memories(scope=SCOPE_USER, scope_id=2)] == ["B 喜欢狗"]


def test_delete_and_clear(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    a, _ = store.upsert(scope=SCOPE_USER, scope_id=1, content="甲")
    store.upsert(scope=SCOPE_USER, scope_id=1, content="乙")
    assert store.delete([a], scope=SCOPE_USER, scope_id=1) == 1
    assert [m.content for m in store.list_memories(scope=SCOPE_USER, scope_id=1)] == ["乙"]
    assert store.clear(scope=SCOPE_USER, scope_id=1) == 1
    assert store.list_memories(scope=SCOPE_USER, scope_id=1) == []


def test_delete_and_set_tier_reject_foreign_scope(tmp_path):
    """delete / set_tier 必须校验归属：不能用编号操作别人的记忆（横向越权）"""
    store = MemoryStore(tmp_path / "m.db")
    a, _ = store.upsert(scope=SCOPE_USER, scope_id=1, content="甲的秘密")
    # 用户 2 尝试删除/改层用户 1 的记忆：应无效果
    assert store.delete([a], scope=SCOPE_USER, scope_id=2) == 0
    assert store.set_tier(a, TIER_PERMANENT, scope=SCOPE_USER, scope_id=2) is False
    assert [m.content for m in store.list_memories(scope=SCOPE_USER, scope_id=1)] == ["甲的秘密"]
    # 归属者本人操作正常
    assert store.set_tier(a, TIER_PERMANENT, scope=SCOPE_USER, scope_id=1) is True
    assert store.delete([a], scope=SCOPE_USER, scope_id=1) == 1


# ──────────────────────────────────────────────────────────────────
# Store：召回
# ──────────────────────────────────────────────────────────────────


def test_recall_prefers_relevant(tmp_path):
    """与问题相关的记忆排在前面"""
    store = MemoryStore(tmp_path / "m.db")
    store.upsert(scope=SCOPE_USER, scope_id=1, content="对方喜欢猫", importance=0.5)
    store.upsert(scope=SCOPE_USER, scope_id=1, content="对方在学 Rust", importance=0.5)
    hits = store.recall(scope=SCOPE_USER, scope_id=1, query="我家猫生病了", limit=5)
    assert hits[0].content == "对方喜欢猫"


def test_recall_always_keeps_permanent(tmp_path):
    """永久记忆哪怕与问题毫不相关也要带上（用户明确要求记住）"""
    store = MemoryStore(tmp_path / "m.db")
    store.upsert(
        scope=SCOPE_USER,
        scope_id=1,
        content="不要香菜",
        tier=TIER_PERMANENT,
        importance=1.0,
    )
    hits = store.recall(scope=SCOPE_USER, scope_id=1, query="帮我写段排序算法", limit=5)
    assert any(m.content == "不要香菜" for m in hits)


def test_recall_skips_irrelevant_low_importance(tmp_path):
    """无交集且不重要的长期记忆不该占用上下文"""
    store = MemoryStore(tmp_path / "m.db")
    store.upsert(scope=SCOPE_USER, scope_id=1, content="对方在学 Rust", importance=0.3)
    assert store.recall(scope=SCOPE_USER, scope_id=1, query="今天天气", limit=5) == []


def test_similarity_and_normalize():
    assert normalize("你好，世界！") == "你好世界"
    assert similarity("喜欢猫", "喜欢猫") == 1.0
    assert 0 < similarity("喜欢猫", "我很喜欢猫") < 1
    assert similarity("猫", "数据库索引") < 0.2


# ──────────────────────────────────────────────────────────────────
# Store：自主演化
# ──────────────────────────────────────────────────────────────────


def test_decay_forgets_stale_long_term_but_spares_permanent(tmp_path):
    """长期记忆随时间衰减直至遗忘；永久记忆豁免"""
    store = MemoryStore(tmp_path / "m.db")
    old = time.time() - 400 * DAY
    long_id, _ = store.upsert(scope=SCOPE_USER, scope_id=1, content="旧事", importance=0.3, now=old)
    perm_id, _ = store.upsert(
        scope=SCOPE_USER,
        scope_id=1,
        content="要永远记住的事",
        tier=TIER_PERMANENT,
        importance=0.2,
        now=old,
    )
    decayed, forgotten = store.decay(half_life_days=30.0, floor=0.15)
    assert decayed == 1  # 只有那条长期记忆参与衰减
    assert forgotten == 1
    assert store.get(long_id) is None
    assert store.get(perm_id) is not None
    assert store.get(perm_id).importance == pytest.approx(0.2)


def test_decay_keeps_recent_memory(tmp_path):
    """刚写入的记忆不该被衰减掉"""
    store = MemoryStore(tmp_path / "m.db")
    mem_id, _ = store.upsert(scope=SCOPE_USER, scope_id=1, content="新事", importance=0.5)
    _, forgotten = store.decay(half_life_days=30.0, floor=0.15)
    assert forgotten == 0
    assert store.get(mem_id) is not None


def test_promote_by_hits_and_importance(tmp_path):
    """反复命中且足够重要的长期记忆升为永久"""
    store = MemoryStore(tmp_path / "m.db")
    mem_id, _ = store.upsert(scope=SCOPE_USER, scope_id=1, content="关键事实", importance=0.9)
    store.reinforce([mem_id], delta=0.0)  # 命中累计到 2
    store.reinforce([mem_id], delta=0.0)  # 3
    assert store.get(mem_id).hits == 3
    assert store.promote(min_importance=0.85, min_hits=3) == 1
    assert store.get(mem_id).tier == TIER_PERMANENT


def test_consolidate_merges_near_duplicates(tmp_path):
    """高度相似的记忆合并，保留更重要的一条"""
    store = MemoryStore(tmp_path / "m.db")
    keep, _ = store.upsert(
        scope=SCOPE_USER, scope_id=1, content="对方喜欢喝冰美式咖啡", importance=0.9
    )
    dup, _ = store.upsert(
        scope=SCOPE_USER, scope_id=1, content="对方喜欢喝冰美式咖啡哦", importance=0.5
    )
    merged = store.consolidate(scope=SCOPE_USER, scope_id=1, threshold=0.8)
    assert merged == 1
    remaining = store.list_memories(scope=SCOPE_USER, scope_id=1)
    assert [m.id for m in remaining] == [keep]
    assert store.get(dup) is None
    # 合并后命中数累加，避免"越合并越弱"
    assert remaining[0].hits >= 2


# ──────────────────────────────────────────────────────────────────
# 事实提炼
# ──────────────────────────────────────────────────────────────────


def test_extract_explicit_memory_is_permanent():
    facts = extract_facts("记住：我不吃香菜")
    assert facts and facts[0].permanent is True
    assert "香菜" in facts[0].content


def test_extract_preference_and_name():
    assert any("喜欢" in f.content for f in extract_facts("我很喜欢猫"))
    assert any("小明" in f.content for f in extract_facts("我叫小明"))


def test_extract_skips_small_talk_and_questions():
    assert extract_facts("今天天气不错") == []
    assert extract_facts("你喜欢什么吗") == []
    # 「叫我」后跟动词短语不是称呼
    assert extract_facts("叫我看看这个") == []


def test_extract_caps_per_message():
    text = "我叫小明，我喜欢猫，我喜欢狗，我的生日是三月五号，记住：我不吃香菜"
    assert len(extract_facts(text)) <= 3


# ──────────────────────────────────────────────────────────────────
# 插件：augment_prompt（召回并注入）
# ──────────────────────────────────────────────────────────────────


async def test_augment_prompt_injects_memories(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._store.upsert(scope=SCOPE_USER, scope_id=7, content="对方喜欢猫", importance=0.8)
    prompt = await plugin.augment_prompt(
        session_key="private:7",
        user_id=7,
        group_id=0,
        query="我家猫不吃饭",
        system_prompt="你是大肥鱼",
    )
    assert prompt.startswith("你是大肥鱼")
    assert "[记忆]" in prompt
    assert "对方喜欢猫" in prompt
    # 注入后应强化命中
    mem = plugin._store.list_memories(scope=SCOPE_USER, scope_id=7)[0]
    assert mem.hits >= 2


async def test_augment_prompt_without_memory_returns_unchanged(tmp_path):
    plugin = make_plugin(tmp_path)
    prompt = await plugin.augment_prompt(
        session_key="private:7", user_id=7, group_id=0, query="你好", system_prompt="原文"
    )
    assert prompt == "原文"


async def test_augment_prompt_respects_char_budget(tmp_path):
    """注入内容不得超过 max_inject_chars"""
    plugin = make_plugin(tmp_path, MemorySettings(max_inject_chars=20, recall_limit=10))
    for i in range(5):
        plugin._store.upsert(
            scope=SCOPE_USER, scope_id=7, content=f"对方喜欢第{i}种东西", importance=0.9
        )
    prompt = await plugin.augment_prompt(
        session_key="private:7", user_id=7, group_id=0, query="喜欢", system_prompt="P"
    )
    assert prompt != "P"  # 确实注入了内容
    # 正文（"记忆块标题"之后的条目行）总长不得超过配置预算
    body = prompt.split("\n")[-5:]
    items = [line for line in body if line.startswith("- ")]
    assert items, "应至少注入一条记忆"
    assert sum(len(line) for line in items) <= 20


async def test_augment_prompt_disabled_plugin(tmp_path):
    plugin = make_plugin(tmp_path, MemorySettings(enabled=False))
    plugin._store.upsert(scope=SCOPE_USER, scope_id=7, content="对方喜欢猫", importance=1.0)
    prompt = await plugin.augment_prompt(
        session_key="private:7", user_id=7, group_id=0, query="猫", system_prompt="P"
    )
    assert prompt == "P"


# ──────────────────────────────────────────────────────────────────
# 插件：manage_history（短期记忆 = 上下文接管）
# ──────────────────────────────────────────────────────────────────


def _flat(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        out += [
            {"role": "user", "content": f"msg{i}"},
            {"role": "assistant", "content": f"ans{i}"},
        ]
    return out


def _policy(enabled: bool = False, keep: int = 1) -> SummaryPolicy:
    return SummaryPolicy(
        enabled=enabled,
        max_messages=8,
        max_tokens=100,
        keep_recent_turns=keep,
        summary_max_tokens=200,
    )


async def test_manage_history_trims_by_count(tmp_path):
    plugin = make_plugin(tmp_path, MemorySettings(extract=False))
    decision = await plugin.manage_history(
        session_key="private:1",
        messages=_flat(6),
        max_messages=4,
        max_tokens=0,
        summary=_policy(),
        estimate=lambda m: 1,
    )
    assert len(decision.messages) == 4
    assert decision.messages[-1]["content"] == "ans5"
    assert decision.rewrite is False


async def test_manage_history_summarizes_when_enabled(tmp_path):
    """摘要启用且超预算：压成一条 system 摘要 + 保留最近原文，并要求重写 DB"""
    plugin = make_plugin(tmp_path)
    plugin.llm = SimpleNamespace(adapter=FakeAdapter(reply="这是摘要"))
    decision = await plugin.manage_history(
        session_key="private:1",
        messages=_flat(5),
        max_messages=100,
        # 真实估算：全量 40 字 > 20 触发压缩；压缩后 3 条 12 字不再超限
        max_tokens=20,
        summary=_policy(enabled=True, keep=1),
        estimate=lambda m: sum(len(x.get("content", "")) for x in m),
    )
    assert decision.rewrite is True
    assert decision.messages[0]["role"] == "system"
    assert "摘要" in decision.messages[0]["content"]
    assert len(decision.messages) >= 3
    # 摘要落库供统计/后续召回
    assert plugin._store.get_digest("private:1") == "这是摘要"


async def test_manage_history_records_summary_usage(tmp_path):
    """摘要产生的 token 用量要入库，且 source=summary（沿用既有统计口径）"""
    plugin = make_plugin(tmp_path)
    plugin.llm = SimpleNamespace(
        adapter=FakeAdapter(reply="摘要", usage={"prompt_tokens": 5, "completion_tokens": 3})
    )
    db = FakeDB()
    plugin.db = db
    await plugin.manage_history(
        session_key="private:1",
        messages=_flat(5),
        max_messages=100,
        max_tokens=10,
        summary=_policy(enabled=True),
        estimate=lambda m: 100,
    )
    assert db.usage and db.usage[-1]["source"] == "summary"
    assert db.usage[-1]["prompt_tokens"] == 5


async def test_manage_history_falls_back_to_hard_trim_when_summary_disabled(tmp_path):
    """摘要关闭时不调模型，直接硬裁剪，且至少保留最近一轮"""
    plugin = make_plugin(tmp_path)
    adapter = FakeAdapter()
    plugin.llm = SimpleNamespace(adapter=adapter)
    decision = await plugin.manage_history(
        session_key="private:1",
        messages=_flat(5),
        max_messages=100,
        max_tokens=10,
        summary=_policy(enabled=False),
        estimate=lambda m: 100,
    )
    assert adapter.calls == []  # 没调用模型
    assert decision.rewrite is False
    assert len(decision.messages) == 2  # 至少留一轮
    assert decision.messages[-1]["content"] == "ans4"


async def test_manage_history_summary_failure_degrades(tmp_path):
    """摘要失败要降级为硬裁剪，不能抛给调用方"""
    plugin = make_plugin(tmp_path)
    plugin.llm = SimpleNamespace(adapter=FakeAdapter(fail=True))
    decision = await plugin.manage_history(
        session_key="private:1",
        messages=_flat(5),
        max_messages=100,
        max_tokens=10,
        summary=_policy(enabled=True),
        estimate=lambda m: 100,
    )
    assert decision.rewrite is False
    assert len(decision.messages) == 2


async def test_manage_history_without_llm_degrades(tmp_path):
    """没有 LLM（如单元测试或降级启动）时不得崩溃"""
    plugin = make_plugin(tmp_path)
    plugin.llm = None
    decision = await plugin.manage_history(
        session_key="private:1",
        messages=_flat(5),
        max_messages=100,
        max_tokens=10,
        summary=_policy(enabled=True),
        estimate=lambda m: 100,
    )
    assert len(decision.messages) == 2


# ──────────────────────────────────────────────────────────────────
# 插件：observe_turn / forget
# ──────────────────────────────────────────────────────────────────


async def test_observe_turn_extracts_long_term_memory(tmp_path):
    plugin = make_plugin(tmp_path)
    await plugin.observe_turn(
        session_key="private:7",
        user_id=7,
        group_id=0,
        user_text="我叫小明，我很喜欢猫",
        assistant_text="记住啦，小明喜欢猫",
    )
    contents = [m.content for m in plugin._store.list_memories(scope=SCOPE_USER, scope_id=7)]
    assert any("小明" in c for c in contents)
    assert any("猫" in c for c in contents)
    # 助手回复不能被当成事实沉淀（那是模型自己的话）
    assert not any("记住啦" in c for c in contents)


async def test_observe_turn_respects_extract_off(tmp_path):
    plugin = make_plugin(tmp_path, MemorySettings(extract=False))
    await plugin.observe_turn(
        session_key="private:7",
        user_id=7,
        group_id=0,
        user_text="我叫小明",
        assistant_text="好",
    )
    assert plugin._store.list_memories(scope=SCOPE_USER, scope_id=7) == []


async def test_forget_clears_session_digest(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin._store.set_digest("private:7", "旧摘要")
    await plugin.forget(session_key="private:7")
    assert plugin._store.get_digest("private:7") == ""


async def test_evolve_is_throttled(tmp_path):
    """演化按间隔节流：同一小时内第二次调用不再衰减"""
    plugin = make_plugin(tmp_path, MemorySettings(evolve_interval_seconds=3600))
    plugin._store.upsert(scope=SCOPE_USER, scope_id=7, content="旧事", importance=0.5)
    await plugin._maybe_evolve(7)
    first = plugin._store.get_meta("last_evolve", "")
    plugin._store.upsert(scope=SCOPE_USER, scope_id=7, content="另一条", importance=0.5)
    await plugin._maybe_evolve(7)
    assert plugin._store.get_meta("last_evolve", "") == first


# ──────────────────────────────────────────────────────────────────
# 配置解析
# ──────────────────────────────────────────────────────────────────


def test_settings_defaults_when_missing():
    s = MemorySettings.from_config(None)
    assert s.enabled is True
    assert s.recall_limit == 8


def test_settings_bad_values_fall_back():
    """笔误不应该让记忆功能整体失效"""
    s = MemorySettings.from_config({"recall_limit": "很多", "decay_floor": 5})
    assert s.recall_limit == 8  # 解析失败 → 默认
    assert s.decay_floor == 1.0  # 越界值被夹回合法区间


def test_settings_clamps_and_casts():
    s = MemorySettings.from_config(
        {"enabled": False, "max_inject_chars": "300", "decay_half_life_days": 7}
    )
    assert s.enabled is False
    assert s.max_inject_chars == 300
    assert s.decay_half_life_days == 7.0


# ──────────────────────────────────────────────────────────────────
# /memory 命令
# ──────────────────────────────────────────────────────────────────


async def test_cmd_add_list_and_stats(tmp_path):
    plugin = make_plugin(tmp_path)
    assert "已记住" in await plugin._cmd_memory(ctx("add 我不吃香菜"))
    assert "我不吃香菜" in await plugin._cmd_memory(ctx("list"))
    assert "长期 1 条" in await plugin._cmd_memory(ctx("stats"))


async def test_cmd_pin_and_unpin(tmp_path):
    plugin = make_plugin(tmp_path)
    await plugin._cmd_memory(ctx("add 怕黑"))
    mem = plugin._store.list_memories(scope=SCOPE_USER, scope_id=100)[0]
    assert "永久" in await plugin._cmd_memory(ctx(f"pin #{mem.id}"))
    assert plugin._store.get(mem.id).tier == TIER_PERMANENT
    assert "长期" in await plugin._cmd_memory(ctx(f"unpin #{mem.id}"))
    assert plugin._store.get(mem.id).tier == TIER_LONG


async def test_cmd_pin_with_text_creates_permanent(tmp_path):
    plugin = make_plugin(tmp_path)
    assert "已永久记住" in await plugin._cmd_memory(ctx("pin 记住我的猫叫团子"))
    mem = plugin._store.list_memories(scope=SCOPE_USER, scope_id=100)[0]
    assert mem.tier == TIER_PERMANENT


async def test_cmd_forget_and_clear(tmp_path):
    plugin = make_plugin(tmp_path)
    await plugin._cmd_memory(ctx("add 甲"))
    mem = plugin._store.list_memories(scope=SCOPE_USER, scope_id=100)[0]
    assert "已忘记" in await plugin._cmd_memory(ctx(f"forget #{mem.id}"))
    await plugin._cmd_memory(ctx("add 乙"))
    assert "已清空" in await plugin._cmd_memory(ctx("clear"))


async def test_cmd_search(tmp_path):
    plugin = make_plugin(tmp_path)
    await plugin._cmd_memory(ctx("add 我喜欢喝冰美式"))
    assert "冰美式" in await plugin._cmd_memory(ctx("search 冰美式"))
    assert "没有相关记忆" in await plugin._cmd_memory(ctx("search 量子力学"))


async def test_cmd_purge_requires_admin(tmp_path):
    """清空全员记忆是破坏性操作，普通用户不行"""
    plugin = make_plugin(tmp_path)
    plugin._store.upsert(scope=SCOPE_USER, scope_id=100, content="甲")
    plugin.bot = SimpleNamespace(config=SimpleNamespace(bot=SimpleNamespace(admin_set={999})))
    assert "仅超级管理员" in await plugin._cmd_memory(ctx("purge"))
    assert "已清空全部记忆" in await plugin._cmd_memory(ctx("purge", user_id=999))
    assert plugin._store.list_memories(scope=SCOPE_USER, scope_id=100) == []


async def test_cmd_help_on_unknown_subcommand(tmp_path):
    plugin = make_plugin(tmp_path)
    out = await plugin._cmd_memory(ctx("whatever"))
    assert "/memory list" in out


# ──────────────────────────────────────────────────────────────────
# 与框架的接缝（LLMManager 真正走插件）
# ──────────────────────────────────────────────────────────────────


async def test_manager_delegates_to_plugin_end_to_end(tmp_path):
    """LLMManager 挂上插件后：注入记忆 → 调模型 → 沉淀新记忆，全链路贯通"""
    from bot.config import LLMConfig
    from bot.core.tasks import await_pending_tasks
    from bot.llm.manager import LLMManager

    class Adapter(FakeAdapter):
        async def chat_detail(
            self, messages, system_prompt=None, max_tokens=2048, temperature=0.7, **kwargs
        ):
            self.calls.append({"system_prompt": system_prompt})
            return SimpleNamespace(content="好呀", usage=None)

    plugin = make_plugin(tmp_path)
    manager = LLMManager(LLMConfig(max_history=5, max_context_tokens=0))
    manager._adapter = Adapter()
    manager.set_memory_provider(plugin)

    assert await manager.chat("我叫小明，我喜欢猫", message_type="private", user_id=7) == "好呀"
    await await_pending_tasks(0.5)  # observe 是后台任务

    # 新记忆已沉淀
    contents = [m.content for m in plugin._store.list_memories(scope=SCOPE_USER, scope_id=7)]
    assert any("小明" in c for c in contents)

    # 第二轮：记忆应被注入到系统提示词
    await manager.chat("我上回说我叫什么？", message_type="private", user_id=7)
    prompt = manager._adapter.calls[-1]["system_prompt"]
    assert "[记忆]" in prompt
    assert "小明" in prompt


# ──────────────────────────────────────────────────────────────────
# 插件生命周期：on_load 真的把框架接管了
# ──────────────────────────────────────────────────────────────────


async def test_on_load_wires_provider_and_on_unload_restores(tmp_path, monkeypatch):
    """on_load 必须把自己挂到 LLMManager 上；on_unload 必须交还控制权

    单元测试都直接塞 _store / _settings，绕过了真实生命周期。这里走一遍
    on_load / on_unload，验证「插件确实接管了框架」以及卸载后不会留下
    指向已卸载实例的引用（否则框架会调用到已关闭的 store）。
    """
    from bot.config import LLMConfig
    from bot.llm.manager import LLMManager
    from bot.llm.memory import FallbackMemoryProvider
    from bot.plugin.protocol.base import PluginBase

    # 插件数据目录由 SDK 按实例解析；测试里指到 tmp_path，避免污染真实数据根
    monkeypatch.setattr(PluginBase, "data_dir", property(lambda self: tmp_path))

    plugin = MemoryPlugin()
    plugin.config = SimpleNamespace(plugins={})
    manager = LLMManager(LLMConfig())
    plugin.llm = manager

    await plugin.on_load()
    try:
        assert manager.memory is plugin, "on_load 未把插件挂到 LLMManager"
        assert plugin.matchers and len(plugin.matchers) == 1, "未注册 /memory 命令"
        assert (tmp_path / "memory.db").exists(), "未在插件数据目录建库"
    finally:
        plugin.db = None
        await plugin.on_unload()

    assert isinstance(manager.memory, FallbackMemoryProvider), (
        "on_unload 未交还控制权，框架仍持有已卸载插件"
    )


# ──────────────────────────────────────────────────────────────────
# 记忆星图：图数据构建 + 页面/接口注册
# ──────────────────────────────────────────────────────────────────


def test_list_all_spans_users(tmp_path):
    """星图要跨用户展示：list_all 不能像 list_memories 那样按用户过滤"""
    store = MemoryStore(tmp_path / "m.db")
    store.upsert(scope=SCOPE_USER, scope_id=1, content="甲喜欢猫", importance=0.9)
    store.upsert(scope=SCOPE_USER, scope_id=2, content="乙喜欢狗", importance=0.5)
    all_items = store.list_all()
    assert {m.scope_id for m in all_items} == {1, 2}
    # 按显著度降序
    assert all_items[0].content == "甲喜欢猫"


def test_build_graph_nodes_and_edges(tmp_path):
    """相似记忆之间连线，不同主题之间不连"""
    from bot.plugin.builtin.memory.graph import build_graph

    store = MemoryStore(tmp_path / "m.db")
    store.upsert(scope=SCOPE_USER, scope_id=1, content="对方喜欢喝冰美式咖啡", importance=0.9)
    store.upsert(scope=SCOPE_USER, scope_id=1, content="对方喜欢喝冰美式", importance=0.7)
    store.upsert(scope=SCOPE_USER, scope_id=2, content="对方在学 Rust 异步编程", importance=0.4)

    g = build_graph(store.list_all(), max_nodes=10, edge_threshold=0.42, now=1_700_000_000)
    assert len(g["nodes"]) == 3
    assert g["stats"]["users"] == 2
    assert g["stats"]["total"] == 3
    # 只有那对「冰美式」相关，应与 Rust 无关
    assert len(g["edges"]) == 1
    edge = g["edges"][0]
    ids = {edge["source"], edge["target"]}
    assert len(ids) == 2
    assert 0.42 <= edge["weight"] <= 1.0
    # 节点字段齐全（前端依赖这些字段画图）
    node = g["nodes"][0]
    for key in ("id", "user", "tier", "content", "importance", "hits", "age_days", "source"):
        assert key in node


def test_build_graph_caps_nodes_and_flags_truncation(tmp_path):
    """超过上限时截断，并如实告知（前端要提示"仅显示前 N 条"）"""
    from bot.plugin.builtin.memory.graph import build_graph

    store = MemoryStore(tmp_path / "m.db")
    for i in range(8):
        store.upsert(
            scope=SCOPE_USER, scope_id=i % 2, content=f"第{i}条互不相干的记忆内容", importance=0.5
        )

    g = build_graph(store.list_all(), max_nodes=5, edge_threshold=0.99)
    assert g["stats"]["shown"] == 5
    assert g["stats"]["total"] == 8
    assert g["stats"]["truncated"] is True


def test_build_graph_limits_edges_per_node(tmp_path):
    """每条记忆最多连几条：否则一个点连几十条线只会糊成一团"""
    from bot.plugin.builtin.memory.graph import build_graph

    store = MemoryStore(tmp_path / "m.db")
    # 内容高度相似 → 两两都可能连线
    for i in range(6):
        store.upsert(
            scope=SCOPE_USER, scope_id=1, content=f"对方喜欢喝冰美式咖啡{i}", importance=0.6
        )

    g = build_graph(store.list_all(), max_nodes=10, edge_threshold=0.3, edges_per_node=2)
    degree: dict[int, int] = {}
    for e in g["edges"]:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    assert degree and max(degree.values()) <= 2


async def test_graph_api_returns_payload(tmp_path):
    """插件接口返回可直接 JSON 序列化的结构"""
    plugin = make_plugin(tmp_path)
    plugin._store.upsert(scope=SCOPE_USER, scope_id=7, content="对方喜欢猫", importance=0.8)

    payload = await plugin._api_graph(None)
    assert set(payload) == {"nodes", "edges", "stats"}
    assert payload["stats"]["shown"] == 1
    assert payload["nodes"][0]["content"] == "对方喜欢猫"


async def test_graph_api_without_store_is_safe():
    """插件未完成加载（无 store）时返回空结构，不抛异常"""
    plugin = MemoryPlugin()
    plugin._store = None
    payload = await plugin._api_graph(None)
    assert payload["nodes"] == [] and payload["stats"]["total"] == 0


async def test_plugin_registers_star_map_page_and_api(tmp_path, monkeypatch):
    """on_load 必须注册「记忆星图」页面与只读接口，且静态目录真实存在"""
    from bot.config import LLMConfig
    from bot.llm.manager import LLMManager
    from bot.plugin.protocol.base import PluginBase

    monkeypatch.setattr(PluginBase, "data_dir", property(lambda self: tmp_path))
    plugin = MemoryPlugin()
    plugin.config = SimpleNamespace(plugins={})
    plugin.llm = LLMManager(LLMConfig())

    await plugin.on_load()
    try:
        pages = plugin._pages
        assert any(p["title"] == "记忆星图" for p in pages), "未注册记忆星图页面"
        # SDK 会按「模块同级 web/」自动探测静态目录；探测不到就是空白页
        web_dir = next(p["static_dir"] for p in pages if p["title"] == "记忆星图")
        assert web_dir and (Path(web_dir) / "index.html").is_file(), f"静态页缺失: {web_dir}"
        assert any(a["path"] == "graph" for a in plugin._apis), "未注册 graph 接口"
    finally:
        plugin.db = None
        await plugin.on_unload()


def test_normalize_keeps_alphanumerics(tmp_path):
    """归一化只能删标点，绝不能吞掉数字与字母

    正则字符类里一个没放对位置的 ``-`` 会构成区间（``'-—'`` 覆盖 ASCII 数字与
    字母），归一化会把「喜欢 Rust」和「喜欢 Python」都变成「喜欢」——两条不同的
    记忆共用一个去重键，被 upsert 当作重复合并，**静默丢数据**。
    这是 1.23 引入的去重键实现里踩到的真实缺陷。
    """
    assert normalize("喜欢 Rust") == "喜欢rust"
    assert normalize("第0条") == "第0条"
    assert normalize("v1.23.0-rc1") == "v1230rc1"
    assert normalize("喜欢 Rust") != normalize("喜欢 Python")

    # 落到存储层：两条仅 ASCII 部分不同的记忆必须各自存在
    store = MemoryStore(tmp_path / "m.db")
    store.upsert(scope=SCOPE_USER, scope_id=1, content="对方在学 Rust", importance=0.5)
    store.upsert(scope=SCOPE_USER, scope_id=1, content="对方在学 Python", importance=0.5)
    contents = sorted(m.content for m in store.list_memories(scope=SCOPE_USER, scope_id=1))
    assert contents == ["对方在学 Python", "对方在学 Rust"]
