"""运行指标导出测试（Prometheus 文本格式）"""

from types import SimpleNamespace

from bot.metrics import render_metrics


class _FakePlugin:
    status = "loaded"


class _FakeManager:
    def __init__(self, metrics=None):
        self.plugins = {"chat": _FakePlugin()}
        self._metrics = metrics if metrics is not None else []

    def get_metrics(self, name):
        return self._metrics


def _fake_bot(**overrides):
    base = {
        "is_running": True,
        "connection": SimpleNamespace(is_connected=True),
        "pending_event_count": 5,
        "dropped_events_total": 1,
        "uptime_seconds": 42.5,
        "event_concurrency_limit": 16,
        "db": SimpleNamespace(pending_messages=2, dropped_messages=0),
        "plugin_manager": _FakeManager(
            [
                {
                    "handler": "chat_handler",
                    "call_count": 3,
                    "error_count": 1,
                    "slow_count": 0,
                    "avg_time_ms": 12.5,
                }
            ]
        ),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def test_up_zero_without_bot():
    """未装配 Bot 时仍输出 up=0，供进程存活探测"""
    out = render_metrics(None)

    assert "qingci_bot_up 0" in _lines(out)
    assert "# TYPE qingci_bot_up gauge" in out
    # 不应泄漏其他指标
    assert "qingci_bot_running" not in out


def test_basic_gauges_and_counters():
    out = render_metrics(_fake_bot())
    lines = _lines(out)

    assert "qingci_bot_up 1" in lines
    assert "qingci_bot_running 1" in lines
    assert "qingci_bot_connected 1" in lines
    assert "qingci_bot_uptime_seconds 42.500" in lines
    assert "qingci_bot_events_pending 5" in lines
    assert "qingci_bot_events_dropped_total 1" in lines
    assert "qingci_bot_event_concurrency_limit 16" in lines
    assert "qingci_bot_message_buffer_pending 2" in lines
    assert "qingci_bot_message_buffer_dropped_total 0" in lines
    assert "qingci_bot_plugins_loaded 1" in lines


def test_matcher_metrics_carry_labels():
    out = render_metrics(_fake_bot())

    assert 'qingci_bot_matcher_calls_total{plugin="chat",matcher="chat_handler"} 3' in _lines(out)
    assert 'qingci_bot_matcher_errors_total{plugin="chat",matcher="chat_handler"} 1' in _lines(out)
    assert 'qingci_bot_matcher_slow_total{plugin="chat",matcher="chat_handler"} 0' in _lines(out)
    assert (
        'qingci_bot_matcher_avg_duration_ms{plugin="chat",matcher="chat_handler"} 12.500'
        in _lines(out)
    )


def test_help_and_type_declared_once_per_metric():
    """同一指标族只应输出一组 HELP/TYPE（重复声明会被 Prometheus 判为格式错误）"""
    out = render_metrics(_fake_bot())

    assert out.count("# TYPE qingci_bot_matcher_calls_total counter") == 1
    assert out.count("# HELP qingci_bot_matcher_calls_total") == 1


def test_label_values_are_escaped():
    """标签值中的引号/反斜杠必须转义，否则整份指标解析失败"""
    bot = _fake_bot(
        plugin_manager=_FakeManager(
            [{"handler": 'evil"name\\x', "call_count": 1, "error_count": 0, "slow_count": 0}]
        )
    )
    out = render_metrics(bot)

    assert 'matcher="evil\\"name\\\\x"' in out


def test_not_running_reports_zero():
    bot = _fake_bot(is_running=False, connection=SimpleNamespace(is_connected=False))
    lines = _lines(render_metrics(bot))

    assert "qingci_bot_running 0" in lines
    assert "qingci_bot_connected 0" in lines


def test_missing_optional_components_do_not_raise():
    """db / plugin_manager 缺失（API-only 进程）时不得抛异常"""
    bot = SimpleNamespace(is_running=False, connection=None, db=None, plugin_manager=None)

    out = render_metrics(bot)

    assert "qingci_bot_up 1" in _lines(out)
    assert "qingci_bot_message_buffer_pending" not in out


def test_manager_failure_is_isolated():
    """单个插件指标采集异常不得影响整份导出"""

    class _BrokenManager(_FakeManager):
        def get_metrics(self, name):
            raise RuntimeError("boom")

    out = render_metrics(_fake_bot(plugin_manager=_BrokenManager()))

    assert "qingci_bot_up 1" in _lines(out)
    assert "qingci_bot_plugins_loaded 1" in _lines(out)
