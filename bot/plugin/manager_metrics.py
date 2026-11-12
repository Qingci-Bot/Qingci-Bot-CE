# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""Matcher 执行指标（PluginManager 的指标职责分组）

Mixin 形式并入 PluginManager：状态（_metrics）由 PluginManager.__init__
统一初始化，本模块只承载方法；对外 API 仍是 manager.record_metric /
manager.get_metrics。
"""

import time
from dataclasses import dataclass

from .protocol.base import PluginBase
from .protocol.matcher import Matcher


@dataclass
class MatcherMetrics:
    """单个 Matcher 的执行指标（含阶段耗时细分）"""

    call_count: int = 0
    total_time_ms: float = 0.0
    error_count: int = 0
    last_call_time: float = 0.0
    # 阶段耗时累计（毫秒）：permission 检查 / rule 检查 / handler 执行
    permission_time_ms: float = 0.0
    rule_time_ms: float = 0.0
    handler_time_ms: float = 0.0
    # 慢调用计数：执行耗时超过 slow_threshold_ms 的次数（未设阈值时恒为 0）
    slow_count: int = 0

    @property
    def avg_time_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_time_ms / self.call_count

    @property
    def avg_handler_time_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.handler_time_ms / self.call_count


class PluginMetricsMixin:
    """指标记录与查询（并入 PluginManager）"""

    # 状态由 PluginManager.__init__ 初始化（mixin 仅声明类型，不做赋值）
    _metrics: dict[str, dict[Matcher, MatcherMetrics]]
    _plugins: dict[str, PluginBase]

    def record_metric(
        self,
        matcher: Matcher,
        elapsed_ms: float,
        is_error: bool = False,
        handler_ms: float | None = None,
        permission_ms: float | None = None,
        rule_ms: float | None = None,
        slow_threshold_ms: float | None = None,
    ) -> None:
        """记录一次 Matcher 执行指标（elapsed_ms 为总耗时，阶段耗时可选细分）

        slow_threshold_ms 非 None 且 elapsed_ms 超过该值（毫秒）时累计 slow_count。
        """
        owner = getattr(matcher, "owner", "__unknown__")
        if owner not in self._metrics:
            self._metrics[owner] = {}
        if matcher not in self._metrics[owner]:
            self._metrics[owner][matcher] = MatcherMetrics()
        m = self._metrics[owner][matcher]
        m.call_count += 1
        m.total_time_ms += elapsed_ms
        if permission_ms is not None:
            m.permission_time_ms += permission_ms
        if rule_ms is not None:
            m.rule_time_ms += rule_ms
        if handler_ms is not None:
            m.handler_time_ms += handler_ms
        if (
            slow_threshold_ms is not None
            and slow_threshold_ms > 0
            and elapsed_ms > slow_threshold_ms
        ):
            m.slow_count += 1
        if is_error:
            m.error_count += 1
        m.last_call_time = time.time()

    def get_metrics(self, plugin_name: str) -> list[dict]:
        """获取指定插件的执行指标"""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return []
        result = []
        for matcher in plugin.matchers or []:
            owner_metrics = self._metrics.get(plugin_name, {})
            m = owner_metrics.get(matcher)
            if m is None:
                continue
            handler_name = getattr(matcher.handler, "__name__", "unknown")
            result.append(
                {
                    "handler": handler_name,
                    "event_type": matcher.event_type,
                    "priority": matcher.priority,
                    "description": (matcher.meta or {}).get("description", ""),
                    "call_count": m.call_count,
                    "avg_time_ms": round(m.avg_time_ms, 2),
                    "total_time_ms": round(m.total_time_ms, 2),
                    "error_count": m.error_count,
                    "last_call_time": m.last_call_time,
                    # 阶段耗时均值（毫秒）：定位到 rule / permission / handler 环节
                    "avg_permission_ms": round(
                        m.permission_time_ms / m.call_count if m.call_count else 0.0, 2
                    ),
                    "avg_rule_ms": round(m.rule_time_ms / m.call_count if m.call_count else 0.0, 2),
                    "avg_handler_ms": round(m.avg_handler_time_ms, 2),
                    "slow_count": m.slow_count,
                }
            )
        return result
