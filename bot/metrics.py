"""运行指标导出 — Prometheus 文本格式（零依赖）

设计约束
--------
- **不查数据库**：``/metrics`` 通常被 15s~60s 间隔高频抓取，任何 DB 查询都会
  给 SQLite 叠加无谓读压力。这里只读进程内状态（计数器 / 队列长度 / 插件指标）。
- **不引入 prometheus_client**：只需要文本格式的子集（counter/gauge + 标签），
  手写约 100 行即可，避免为一个端点增加依赖与启动开销。
- **失败不抛异常**：指标采集绝不能影响被采集的进程，单个指标取值失败时跳过该项。

暴露内容见 ``render_metrics``。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core.bot import QingciBot

logger = logging.getLogger("qingci-bot.metrics")

# Prometheus 文本格式版本（Content-Type 中声明）
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label(value: Any) -> str:
    """转义标签值：反斜杠、双引号、换行"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(value: float) -> str:
    """数值格式化：整数不带小数点，浮点保留 3 位"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.3f}"


class _Collector:
    """按 Prometheus 文本格式累积指标（HELP/TYPE 每组只输出一次）"""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._declared: set[str] = set()

    def _declare(self, name: str, help_text: str, type_: str) -> None:
        if name in self._declared:
            return
        self._declared.add(name)
        self._lines.append(f"# HELP {name} {help_text}")
        self._lines.append(f"# TYPE {name} {type_}")

    def gauge(self, name: str, value: float, help_text: str, labels: dict | None = None) -> None:
        self._declare(name, help_text, "gauge")
        self._lines.append(f"{name}{self._labels(labels)} {_fmt(value)}")

    def counter(self, name: str, value: float, help_text: str, labels: dict | None = None) -> None:
        self._declare(name, help_text, "counter")
        self._lines.append(f"{name}{self._labels(labels)} {_fmt(value)}")

    @staticmethod
    def _labels(labels: dict | None) -> str:
        if not labels:
            return ""
        body = ",".join(f'{k}="{_escape_label(v)}"' for k, v in labels.items())
        return "{" + body + "}"

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def render_metrics(bot: QingciBot | None) -> str:
    """渲染全部指标（bot 为 None 时只输出 up=0，供进程存活探测）"""
    from . import __version__

    c = _Collector()
    c.gauge("qingci_bot_up", 1 if bot is not None else 0, "进程存活（1=已装配 Bot 实例）")
    c.gauge(
        "qingci_bot_info",
        1,
        "构建信息（version 标签）",
        {"version": __version__},
    )

    if bot is None:
        return c.render()

    # ---- 运行状态 ----
    c.gauge("qingci_bot_running", getattr(bot, "is_running", False), "Bot 是否处于运行状态")
    connected = False
    connection = getattr(bot, "connection", None)
    if connection is not None:
        connected = bool(getattr(connection, "is_connected", False))
    c.gauge("qingci_bot_connected", connected, "协议端连接是否就绪")
    c.gauge("qingci_bot_uptime_seconds", getattr(bot, "uptime_seconds", 0.0), "运行时长（秒）")

    # ---- 事件处理 ----
    c.gauge(
        "qingci_bot_events_pending",
        getattr(bot, "pending_event_count", 0),
        "排队 + 执行中的事件数",
    )
    c.counter(
        "qingci_bot_events_dropped_total",
        getattr(bot, "dropped_events_total", 0),
        "因积压超限被丢弃的事件总数",
    )
    c.gauge(
        "qingci_bot_event_concurrency_limit",
        getattr(bot, "event_concurrency_limit", 0),
        "事件并发上限（bot.event_concurrency）",
    )

    # ---- 消息写入缓冲 ----
    db = getattr(bot, "db", None)
    if db is not None:
        c.gauge(
            "qingci_bot_message_buffer_pending",
            getattr(db, "pending_messages", 0),
            "待落库消息数（write-behind 缓冲）",
        )
        c.counter(
            "qingci_bot_message_buffer_dropped_total",
            getattr(db, "dropped_messages", 0),
            "因缓冲溢出被丢弃的消息总数",
        )

    # ---- 插件与 Matcher ----
    manager = getattr(bot, "plugin_manager", None)
    if manager is not None:
        plugins = getattr(manager, "plugins", {}) or {}
        loaded = 0
        for plugin in plugins.values():
            # PluginStatus.LOADED 的值为 "loaded"；直接比字符串避免循环导入
            if getattr(plugin, "status", "") == "loaded":
                loaded += 1
        c.gauge("qingci_bot_plugins_loaded", loaded, "已加载插件数")

        for name in plugins:
            try:
                metrics = manager.get_metrics(name) or []
            except Exception:
                # 单个插件的指标异常不影响整体导出
                logger.debug(f"采集插件 {name} 指标失败", exc_info=True)
                continue
            for item in metrics:
                labels = {"plugin": name, "matcher": item.get("handler", "unknown")}
                c.counter(
                    "qingci_bot_matcher_calls_total",
                    item.get("call_count", 0),
                    "Matcher 调用次数",
                    labels,
                )
                c.counter(
                    "qingci_bot_matcher_errors_total",
                    item.get("error_count", 0),
                    "Matcher 执行异常次数",
                    labels,
                )
                c.counter(
                    "qingci_bot_matcher_slow_total",
                    item.get("slow_count", 0),
                    "Matcher 慢调用次数（超过 log.slow_handler_threshold_ms）",
                    labels,
                )
                c.gauge(
                    "qingci_bot_matcher_avg_duration_ms",
                    item.get("avg_time_ms", 0.0),
                    "Matcher 平均耗时（毫秒）",
                    labels,
                )

    return c.render()
