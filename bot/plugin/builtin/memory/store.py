# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""记忆存储 —— 插件自有的 SQLite 库（长期 / 永久两层落盘，短期在会话表里）

## 为什么用独立 SQLite 而不是框架的 MySQL 式会话表

记忆是**插件的能力**，它的表结构会随演化策略频繁调整（显著性、命中次数、
衰减曲线、合并阈值）。放进框架数据库就得每次改表都写 alembic 迁移，还会让
「卸载插件」变成「框架里留一堆孤儿表」。所以这里把记忆落在
``data_dir/memory.db``：插件拥有自己的数据，宿主 purge 插件时一并删除，
框架侧不出现任何记忆相关的表。

## 三层记忆的落点

- **短期**：就是当前会话的对话窗口，由 ``LLMManager`` 的内存/会话表承载，
  插件通过 ``manage_history`` 参与裁剪与摘要；本库只额外存一份
  ``session_digest``（当前会话的滚动摘要），供召回与统计使用。
- **长期**（tier='long'）：从对话里提炼的事实/偏好，带显著性，会随久未命中
  而衰减，低于下限即遗忘。
- **永久**（tier='permanent'）：用户显式要求记住的内容（``/memory pin`` 或
  句中出现"记住/别忘"），不参与衰减与遗忘。

## 并发

SQLite 单写入者模型：所有写操作经同一把可重入锁串行化，连接用
``check_same_thread=False`` 并开 WAL——插件侧的调用可能来自
``asyncio.to_thread`` 的不同线程。
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

TIER_LONG = "long"
TIER_PERMANENT = "permanent"
SCOPE_USER = "user"
SCOPE_GROUP = "group"

# 归一化时剔除的噪声（标点/空白），仅用于去重键，不改变展示内容。
#
# 注意 `\u2014-` 这个结尾：破折号后面紧跟的 `-` 必须放在字符类末尾（或转义），
# 否则正则会把它读成区间「从 ' 到 —」，而该区间覆盖数字与英文字母——归一化会
# 把「喜欢 Rust」和「喜欢 Python」都变成「喜欢」，两条不同的记忆被当成同一条
# 合并掉。这个 bug 被 test_normalize_keeps_alphanumerics 锁死。
_NOISE = re.compile(r'[\s，。！？、；：“”‘’（）()\[\]【】,.!?;:~`"\'\u2014-]+')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT    NOT NULL,
    scope_id    INTEGER NOT NULL,
    tier        TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    norm_key    TEXT    NOT NULL,
    importance  REAL    NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    last_hit_at REAL    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'auto',
    UNIQUE(scope, scope_id, norm_key)
);
CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope, scope_id, tier);
CREATE INDEX IF NOT EXISTS idx_mem_decay ON memories(tier, last_hit_at);

CREATE TABLE IF NOT EXISTS session_digest (
    session_key TEXT PRIMARY KEY,
    digest      TEXT    NOT NULL DEFAULT '',
    turn_count  INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 情绪状态：与 memories 刻意分表。情绪不是记忆，不该出现在 /memory list
-- 或星图里；它的四个维度是连续值，演化规则也与记忆（显著性/命中/归并）无关。
CREATE TABLE IF NOT EXISTS emotions (
    scope      TEXT    NOT NULL,
    scope_id   INTEGER NOT NULL,
    mood       REAL    NOT NULL DEFAULT 0,
    energy     REAL    NOT NULL DEFAULT 0,
    affinity   REAL    NOT NULL DEFAULT 0.1,
    patience   REAL    NOT NULL DEFAULT 0.7,
    updated_at REAL    NOT NULL,
    turns      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, scope_id)
);
"""


@dataclass(frozen=True, slots=True)
class Memory:
    """一条记忆（长期或永久）"""

    id: int
    scope: str
    scope_id: int
    tier: str
    content: str
    importance: float
    hits: int
    created_at: float
    last_hit_at: float
    source: str

    @property
    def is_permanent(self) -> bool:
        return self.tier == TIER_PERMANENT


def normalize(text: str) -> str:
    """归一化为去重键：去标点空白、统一小写"""
    return _NOISE.sub("", text).lower()


def _bigrams(text: str) -> set[str]:
    """字符二元组集合（中文无需分词即可算重叠度）"""
    clean = normalize(text)
    if len(clean) < 2:
        return {clean} if clean else set()
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


def _unigrams(text: str) -> set[str]:
    return set(normalize(text))


def similarity(a: str, b: str) -> float:
    """两条文本的相似度，0..1（见 :func:`similarity_prepared` 的说明）"""
    return similarity_prepared(prepare(a), prepare(b))


def prepare(text: str) -> tuple[set[str], set[str]]:
    """预处理一条文本为 (单字集, 二元组集)

    星图要在 N 条记忆间两两算相似度（N=240 时约 2.9 万对）。每次调用
    ``similarity`` 都重新 normalize + 切词的话，单次请求要多花一两秒；
    预处理一次、复用集合，成本降到纯集合运算。
    """
    return _unigrams(text), _bigrams(text)


def similarity_prepared(a: tuple[set[str], set[str]], b: tuple[set[str], set[str]]) -> float:
    """基于预处理结果计算相似度，0..1

    由两部分加权而成：

    - **字符包含度**（unigram 交集 / 较短一方的大小）：中文短句里同一个概念
      常以不同词组出现（"猫" 与 "我家猫生病了"），纯二元组 Jaccard 会判成 0，
      召回直接失效；包含度对这种"一方是另一方子集"的情形更敏感。
    - **二元组 Jaccard**：惩罚语序/搭配差异，避免"猫"和"猫粮"被当成同一件事。

    两者各占一半：既能召回，又不至于把无关内容并进来。用于召回排序、写入
    时的重复合并判定，以及记忆星图的连线强度。
    """
    sa_uni, sa_bi = a
    sb_uni, sb_bi = b
    if not sa_uni or not sb_uni:
        return 0.0

    uni_inter = len(sa_uni & sb_uni)
    containment = uni_inter / max(1, min(len(sa_uni), len(sb_uni)))

    bi_union = sa_bi | sb_bi
    jaccard = len(sa_bi & sb_bi) / len(bi_union) if bi_union else 0.0

    return 0.5 * containment + 0.5 * jaccard


class MemoryStore:
    """记忆库（同步接口；插件侧用 ``asyncio.to_thread`` 调用以免阻塞事件循环）"""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ---------- 连接与 schema ----------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=10.0)
            conn.row_factory = sqlite3.Row
            # WAL：允许读不阻塞写；NORMAL 同步在记忆场景足够（丢了也只是少记一条）
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ---------- 写入 ----------

    def upsert(
        self,
        *,
        scope: str,
        scope_id: int,
        content: str,
        tier: str = TIER_LONG,
        importance: float = 0.5,
        source: str = "auto",
        reinforce: float = 0.15,
        now: float | None = None,
    ) -> tuple[int, bool]:
        """写入一条记忆；同键已存在则强化而非新增

        Returns:
            (记忆 id, 是否新建)。已存在时提升 importance（封顶 1.0）并累加 hits。
        """
        ts = time.time() if now is None else now
        key = normalize(content)
        if not key:
            raise ValueError("记忆内容不能为空")
        content = content.strip()
        importance = max(0.0, min(1.0, importance))

        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT id, tier, importance FROM memories "
                "WHERE scope=? AND scope_id=? AND norm_key=?",
                (scope, scope_id, key),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO memories "
                    "(scope, scope_id, tier, content, norm_key, importance, hits, "
                    " created_at, updated_at, last_hit_at, source) "
                    "VALUES (?,?,?,?,?,?,1,?,?,?,?)",
                    (scope, scope_id, tier, content, key, importance, ts, ts, ts, source),
                )
                conn.commit()
                return int(cur.lastrowid or 0), True

            # 已存在：取较高 tier（永久不会被长期覆盖），显著性递增加强
            new_tier = TIER_PERMANENT if TIER_PERMANENT in (row["tier"], tier) else TIER_LONG
            boosted = min(1.0, float(row["importance"]) + reinforce)
            conn.execute(
                "UPDATE memories SET tier=?, importance=?, hits=hits+1, "
                "updated_at=?, last_hit_at=? WHERE id=?",
                (new_tier, boosted, ts, ts, row["id"]),
            )
            conn.commit()
            return int(row["id"]), False

    def reinforce(self, ids: list[int], *, delta: float = 0.1, now: float | None = None) -> None:
        """召回后强化：命中次数 +1、显著性提升（这构成"越用越牢"的自主演化）"""
        if not ids:
            return
        ts = time.time() if now is None else now
        with self._lock:
            conn = self._connect()
            conn.executemany(
                "UPDATE memories SET hits=hits+1, importance=MIN(1.0, importance+?), "
                "last_hit_at=? WHERE id=?",
                [(delta, ts, i) for i in ids],
            )
            conn.commit()

    def set_tier(self, mem_id: int, tier: str, *, now: float | None = None) -> bool:
        """改层级（pin 升为永久 / unpin 降回长期）"""
        ts = time.time() if now is None else now
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE memories SET tier=?, updated_at=?, last_hit_at=? WHERE id=?",
                (tier, ts, ts, mem_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with self._lock:
            conn = self._connect()
            marks = ",".join("?" * len(ids))
            cur = conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", ids)
            conn.commit()
            return cur.rowcount

    def clear(self, *, scope: str, scope_id: int, tier: str | None = None) -> int:
        sql = "DELETE FROM memories WHERE scope=? AND scope_id=?"
        args: list[object] = [scope, scope_id]
        if tier:
            sql += " AND tier=?"
            args.append(tier)
        with self._lock:
            conn = self._connect()
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.rowcount

    def purge_all(self) -> int:
        """清空所有记忆（仅 SUPERUSER 的维护命令使用）"""
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM memories")
            conn.commit()
            return cur.rowcount

    # ---------- 读取 ----------

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        return Memory(
            id=int(row["id"]),
            scope=str(row["scope"]),
            scope_id=int(row["scope_id"]),
            tier=str(row["tier"]),
            content=str(row["content"]),
            importance=float(row["importance"]),
            hits=int(row["hits"]),
            created_at=float(row["created_at"]),
            last_hit_at=float(row["last_hit_at"]),
            source=str(row["source"]),
        )

    def list_memories(
        self, *, scope: str, scope_id: int, tier: str | None = None, limit: int = 200
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE scope=? AND scope_id=?"
        args: list[object] = [scope, scope_id]
        if tier:
            sql += " AND tier=?"
            args.append(tier)
        sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._connect().execute(sql, args).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def get(self, mem_id: int) -> Memory | None:
        with self._lock:
            row = self._connect().execute("SELECT * FROM memories WHERE id=?", (mem_id,)).fetchone()
        return self._row_to_memory(row) if row else None

    def list_all(self, *, tier: str | None = None, limit: int = 300) -> list[Memory]:
        """跨用户列出记忆（按重要度降序）

        供管理端可视化使用（记忆星图要把所有人的记忆摆在一起看）。与
        :meth:`list_memories` 的区别只有一点：不按 scope_id 过滤。
        """
        sql = "SELECT * FROM memories"
        args: list[object] = []
        if tier:
            sql += " WHERE tier=?"
            args.append(tier)
        sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._connect().execute(sql, args).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def stats(self, *, scope: str, scope_id: int) -> dict[str, int | float]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT tier, COUNT(*) AS n, AVG(importance) AS imp, SUM(hits) AS hits "
                "FROM memories WHERE scope=? AND scope_id=? GROUP BY tier",
                (scope, scope_id),
            ).fetchall()
        out: dict[str, int | float] = {"long": 0, "permanent": 0, "total": 0, "avg_importance": 0.0}
        total_imp = 0.0
        total_n = 0
        for r in rows:
            out[str(r["tier"])] = int(r["n"])
            out["total"] = int(out["total"]) + int(r["n"])
            total_imp += float(r["imp"] or 0.0) * int(r["n"])
            total_n += int(r["n"])
        if total_n:
            out["avg_importance"] = round(total_imp / total_n, 3)
        return out

    # ---------- 召回 ----------

    def recall(
        self,
        *,
        scope: str,
        scope_id: int,
        query: str,
        limit: int = 8,
        now: float | None = None,
        recency_tau_days: float = 14.0,
        weight_sim: float = 0.55,
        weight_importance: float = 0.30,
        weight_recency: float = 0.15,
    ) -> list[Memory]:
        """按「相关度 + 显著性 + 新鲜度」召回，永久记忆恒定保留在结果最前

        永久记忆不参与相关性淘汰（用户明确要求"记住"，哪怕这句问话不相关
        也该带上），但仍按重要性排序，并占用召回名额。
        """
        ts = time.time() if now is None else now
        candidates = self.list_memories(scope=scope, scope_id=scope_id, limit=500)
        if not candidates:
            return []

        scored: list[tuple[float, Memory]] = []
        for m in candidates:
            age_days = max(0.0, (ts - m.last_hit_at) / 86400.0)
            recency = math.exp(-age_days / max(recency_tau_days, 0.5))
            sim = similarity(query, m.content) if query else 0.0
            if m.is_permanent:
                # 永久记忆：相关度不设门槛，直接用重要性+新鲜度排序
                score = 1.0 + weight_importance * m.importance + weight_recency * recency
            else:
                score = (
                    weight_sim * sim + weight_importance * m.importance + weight_recency * recency
                )
                # 与问题毫无交集且显著性不高的长期记忆不必占用上下文
                if sim <= 0.0 and m.importance < 0.6:
                    continue
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]

    # ---------- 演化 ----------

    def decay(
        self,
        *,
        now: float | None = None,
        half_life_days: float = 30.0,
        floor: float = 0.15,
    ) -> tuple[int, int]:
        """对长期记忆做指数衰减，低于下限的遗忘

        永久记忆不参与（用户说"记住这个"就是承诺，不该被时间冲掉）。

        Returns:
            (参与衰减的条数, 被遗忘的条数)
        """
        if half_life_days <= 0:
            return 0, 0
        ts = time.time() if now is None else now
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, importance, last_hit_at FROM memories WHERE tier=?",
                (TIER_LONG,),
            ).fetchall()
            updates: list[tuple[float, int]] = []
            for r in rows:
                age_days = max(0.0, (ts - float(r["last_hit_at"])) / 86400.0)
                factor = 0.5 ** (age_days / half_life_days)
                new_imp = float(r["importance"]) * factor
                updates.append((new_imp, int(r["id"])))
            if not updates:
                return 0, 0
            conn.executemany("UPDATE memories SET importance=? WHERE id=?", updates)
            cur = conn.execute(
                "DELETE FROM memories WHERE tier=? AND importance < ?", (TIER_LONG, floor)
            )
            conn.commit()
            forgotten = cur.rowcount
        return len(updates), forgotten

    def promote(
        self, *, min_importance: float = 0.85, min_hits: int = 3, now: float | None = None
    ) -> int:
        """把反复命中且足够重要的长期记忆升为永久（自主演化的"固化"一步）

        注意：这是**自动**晋升，只在本插件开启 ``auto_promote`` 时调用；
        用户手动 pin 走 set_tier。
        """
        ts = time.time() if now is None else now
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE memories SET tier=?, updated_at=? "
                "WHERE tier=? AND importance>=? AND hits>=?",
                (TIER_PERMANENT, ts, TIER_LONG, min_importance, min_hits),
            )
            conn.commit()
            return cur.rowcount

    def consolidate(self, *, scope: str, scope_id: int, threshold: float = 0.9) -> int:
        """合并高度相似的记忆，保留更重要的一条（自主演化的"归并"一步）

        避免同一件事被对话反复提炼成十几条近似记录，把上下文预算吃光。
        """
        items = self.list_memories(scope=scope, scope_id=scope_id, limit=500)
        if len(items) < 2:
            return 0
        items.sort(key=lambda m: (m.is_permanent, m.importance, m.hits), reverse=True)
        removed: list[int] = []
        survivors: list[Memory] = []
        for m in items:
            dup = next(
                (s for s in survivors if similarity(s.content, m.content) >= threshold), None
            )
            if dup is None:
                survivors.append(m)
                continue
            removed.append(m.id)
            # 把被合并者的命中次数计入幸存者，避免"越合并越弱"
            self.reinforce([dup.id], delta=0.0)
        if removed:
            self.delete(removed)
        return len(removed)

    # ---------- 会话摘要 ----------

    def set_turn(self, session_key: str, *, turns: int = 1, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO session_digest(session_key, digest, turn_count, updated_at) "
                "VALUES (?, '', ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET "
                "turn_count = turn_count + ?, updated_at = ?",
                (session_key, turns, ts, turns, ts),
            )
            conn.commit()

    def set_digest(self, session_key: str, digest: str, *, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO session_digest(session_key, digest, turn_count, updated_at) "
                "VALUES (?, ?, 0, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET digest=?, updated_at=?",
                (session_key, digest, ts, digest, ts),
            )
            conn.commit()

    def get_digest(self, session_key: str) -> str:
        with self._lock:
            row = (
                self._connect()
                .execute("SELECT digest FROM session_digest WHERE session_key=?", (session_key,))
                .fetchone()
            )
        return str(row["digest"]) if row else ""

    def forget_session(self, session_key: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM session_digest WHERE session_key=?", (session_key,))
            conn.commit()

    # ---------- meta（演化节流时间戳等） ----------

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
                (key, value, value),
            )
            conn.commit()

    # ============ 情绪状态 ============

    def get_emotion(
        self, *, scope: str = SCOPE_USER, scope_id: int
    ) -> tuple[float, float, float, float, float, int] | None:
        """读取情绪原始行；无记录返回 None（调用方用基线兜底）"""
        with self._lock:
            row = (
                self._connect()
                .execute(
                    "SELECT mood, energy, affinity, patience, updated_at, turns "
                    "FROM emotions WHERE scope=? AND scope_id=?",
                    (scope, scope_id),
                )
                .fetchone()
            )
        if row is None:
            return None
        return (
            float(row["mood"]),
            float(row["energy"]),
            float(row["affinity"]),
            float(row["patience"]),
            float(row["updated_at"]),
            int(row["turns"]),
        )

    def save_emotion(
        self,
        *,
        scope: str = SCOPE_USER,
        scope_id: int,
        row: tuple[float, float, float, float, float, int],
    ) -> None:
        """整行覆盖写入（情绪是"当前状态"，没有历史值需要保留）"""
        mood, energy, affinity, patience, updated_at, turns = row
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO emotions(scope, scope_id, mood, energy, affinity, patience, updated_at, turns) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scope, scope_id) DO UPDATE SET "
                "mood=excluded.mood, energy=excluded.energy, affinity=excluded.affinity, "
                "patience=excluded.patience, updated_at=excluded.updated_at, turns=excluded.turns",
                (scope, scope_id, mood, energy, affinity, patience, updated_at, turns),
            )
            conn.commit()

    def clear_emotion(self, *, scope: str = SCOPE_USER, scope_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM emotions WHERE scope=? AND scope_id=?", (scope, scope_id))
            conn.commit()

    def clear_all_emotions(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM emotions")
            conn.commit()


__all__ = [
    "SCOPE_GROUP",
    "SCOPE_USER",
    "TIER_LONG",
    "TIER_PERMANENT",
    "Memory",
    "MemoryStore",
    "normalize",
    "prepare",
    "similarity",
    "similarity_prepared",
]
