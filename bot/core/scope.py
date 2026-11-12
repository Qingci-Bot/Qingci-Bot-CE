# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""应用作用域：进程级服务引用的单一载体（P1）

把散落在各模块的 ``global _xxx`` 收敛到一个显式对象，便于类型化访问、
集中清理（``reset()``，测试隔离用）以及按测试注入独立作用域（``set_scope()``）。

边界（改动前先读）：这里只放「进程唯一、跨模块访问、可被替换」的服务引用。
以下内容**不**放进作用域，留在各自模块更合适：

- 同步原语（``threading.Lock`` / ``asyncio.Lock``）
- 模块内缓存（``api/auth.py`` 的 key 缓存、``api/routes/config.py`` 的 fallback）
- 路径解析状态（``bot/paths.py`` 已有自己的 setter / reset）
- 后台任务句柄（``api/server.py`` 的 runlog pump）
- 运行日志环形缓冲（``bot/logformat.py`` 的 ``_runlog_store``，常量式实例）

``reset()`` 只清空引用，**不**释放资源；需要 dispose 引擎或摘除日志 handler 时，
仍走各模块原有的 ``dispose_engine()`` / ``_detach_runlog_handler()``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AppScope:
    """进程级服务引用集合

    字段用 ``Any`` 标注是刻意的：``bot/core/`` 不应反向依赖 db / llm / logformat
    等具体模块，具体类型由各自的入口函数负责。
    """

    bot_container: Any | None = None  # DIContainer
    engine: Any | None = None  # sqlalchemy AsyncEngine
    session_factory: Any | None = None  # async sessionmaker
    litellm: Any | None = None  # 延迟导入的 litellm 模块
    runlog_handler: Any | None = None  # logging.Handler

    def reset(self) -> None:
        """清空全部引用（测试隔离 / 作用域替换前调用，不释放资源）"""
        self.bot_container = None
        self.engine = None
        self.session_factory = None
        self.litellm = None
        self.runlog_handler = None


_default_scope = AppScope()


def get_scope() -> AppScope:
    """返回当前进程的默认作用域"""
    return _default_scope


def set_scope(scope: AppScope | None = None) -> AppScope:
    """替换当前作用域（测试注入 / 进程重启），返回新作用域

    传入 None 时创建一个全新的空作用域。这是本模块唯一的模块级可变状态，
    其余模块都通过 ``get_scope()`` 读写，不再各自声明 ``global``。
    """
    global _default_scope
    _default_scope = scope if scope is not None else AppScope()
    return _default_scope


def reset_scope() -> None:
    """清空当前作用域的全部引用（不释放资源）"""
    get_scope().reset()
