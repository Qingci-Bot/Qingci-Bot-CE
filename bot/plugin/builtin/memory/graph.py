# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""记忆星图的数据构建（纯函数，无 IO，便于测试）

## 星图要表达什么

记忆不是一个平铺的列表，它有三层含义希望一眼看出来：

1. **谁** —— 每个人一团（星座）。位置由前端按 user 分簇，这里只给归属。
2. **多重要** —— 显著度决定星的大小；命中次数决定光晕。
3. **什么关系** —— 语义相近的记忆之间连线（相似度即连线长度/权重）。

因此这里输出的是「点 + 边 + 统计」，具体画法交给前端（Canvas）。刻意不引入
图布局库：力导向布局在几十行内可写完，还能离线运行（桌面版无网络）。

## 规模控制

两两算相似度是 O(n²)。这里用三道闸把最坏情况压住：

- ``max_nodes`` 限制参与计算的条目数（按显著度取前 N）
- 预处理文本集合（``prepare``），避免每对都重新分词
- 每个节点只保留最强的 ``edges_per_node`` 条边（星图是给人看的，
  一个点连 50 条线只会糊成一团）

被截断的规模会在返回值的 ``truncated`` 里如实告知，前端可提示「仅显示前 N 条」。
"""

from __future__ import annotations

import time

from .store import TIER_PERMANENT, Memory, prepare, similarity_prepared

# 低于该相似度不画线：太多弱连接会让星图变成毛球
DEFAULT_EDGE_THRESHOLD = 0.42
# 每个节点最多保留几条连线
DEFAULT_EDGES_PER_NODE = 3
MAX_NODES = 240


def build_graph(
    memories: list[Memory],
    *,
    max_nodes: int = MAX_NODES,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
    edges_per_node: int = DEFAULT_EDGES_PER_NODE,
    now: float | None = None,
) -> dict:
    """把记忆列表构建成星图数据（nodes / edges / stats）

    Args:
        memories: 已按需筛选好的记忆（通常来自 ``MemoryStore.list_all``）
        max_nodes: 参与展示与连线的最大节点数
        edge_threshold: 相似度达到该值才连线
        edges_per_node: 单节点保留的最大连线数
        now: 当前时间戳（便于测试注入）
    """
    ts = time.time() if now is None else now
    total = len(memories)
    kept = memories[:max_nodes]

    nodes = [
        {
            "id": m.id,
            "user": str(m.scope_id),
            "tier": m.tier,
            "content": m.content,
            "importance": round(float(m.importance), 3),
            "hits": int(m.hits),
            "source": m.source,
            # 天为单位：前端用来做「新旧」的视觉区分（越新越亮）
            "age_days": round(max(0.0, (ts - m.created_at) / 86400.0), 2),
        }
        for m in kept
    ]

    # 预处理一次，后面两两比较只做集合运算
    prepared = [prepare(m.content) for m in kept]
    candidates: list[tuple[float, int, int]] = []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            w = similarity_prepared(prepared[i], prepared[j])
            if w >= edge_threshold:
                candidates.append((w, i, j))

    # 每点只留最强的若干条：按权重降序取，任一端的配额用完就跳过这条边
    candidates.sort(reverse=True)
    quota: dict[int, int] = {}
    edges: list[dict] = []
    for w, i, j in candidates:
        qi, qj = quota.get(i, 0), quota.get(j, 0)
        if qi >= edges_per_node or qj >= edges_per_node:
            continue
        quota[i], quota[j] = qi + 1, qj + 1
        edges.append(
            {
                "source": kept[i].id,
                "target": kept[j].id,
                "weight": round(w, 3),
            }
        )

    permanent = sum(1 for m in kept if m.tier == TIER_PERMANENT)
    users = {str(m.scope_id) for m in kept}
    stats = {
        "total": total,
        "shown": len(kept),
        "permanent": permanent,
        "long": len(kept) - permanent,
        "users": len(users),
        "edges": len(edges),
        "avg_importance": (
            round(sum(float(m.importance) for m in kept) / len(kept), 3) if kept else 0.0
        ),
        "truncated": total > len(kept),
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}


__all__ = ["DEFAULT_EDGE_THRESHOLD", "DEFAULT_EDGES_PER_NODE", "MAX_NODES", "build_graph"]
