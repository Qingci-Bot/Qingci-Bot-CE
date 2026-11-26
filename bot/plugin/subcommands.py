# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""命令的子命令说明 —— 供 /help 展开显示

## 为什么需要

``on_command`` 的 description 只有一行。像 ``/memory list|add|pin|unpin|...``
这样的命令，用户在 /help 里只能看到那一串竖线分隔的名字，看不出每个子命令
做什么——观感就是「命令显示不全」。

SDK 的 ``subcommands`` 参数是**路由**（子命令 → handler），不承载说明文字；
因此这里约定用 meta 的一个独立键 ``subcommand_help`` 传展示信息，由 help 插件
读取并按插件分组展开。

## 用法

::

    self.matchers.append(
        with_subcommands(
            on_command("memory", ...)(self._cmd_memory),
            {"list": "列出记忆", "add <内容>": "手动记一条"},
        )
    )

插件不声明时行为完全不变（help 只显示命令行本身），因此这是纯增量能力。
"""

from __future__ import annotations

from typing import Any


def with_subcommands(matcher: Any, mapping: dict[str, str]) -> Any:
    """把子命令说明挂到匹配器的 meta 上（返回同一个对象，便于链式书写）"""
    meta = getattr(matcher, "meta", None)
    if isinstance(meta, dict):
        meta["subcommand_help"] = dict(mapping)
    return matcher


__all__ = ["with_subcommands"]
