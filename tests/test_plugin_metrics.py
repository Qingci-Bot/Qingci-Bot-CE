"""插件指标 slow_count 测试 — record_metric/get_metrics 的慢调用计数"""

import types

from bot.plugin.manager import PluginManager


class _FakeMatcher:
    """最小 Matcher 兼容对象（record_metric 以实例为字典键，须可哈希）"""

    def __init__(self, owner: str = "plug", handler_name: str = "handler"):
        self.owner = owner
        self.handler = types.SimpleNamespace(__name__=handler_name)
        self.event_type = "message"
        self.priority = 1
        self.meta = {"description": "test"}


def _manager_with_matcher(name: str = "plug"):
    matcher = _FakeMatcher(owner=name)
    mgr = PluginManager()
    mgr._plugins[name] = types.SimpleNamespace(matchers=[matcher], name=name)
    return mgr, matcher


def test_record_metric_counts_slow_calls():
    mgr, matcher = _manager_with_matcher()
    # 超阈值：计入 slow_count
    mgr.record_metric(matcher, elapsed_ms=200, slow_threshold_ms=100)
    # 未超阈值：不计入
    mgr.record_metric(matcher, elapsed_ms=50, slow_threshold_ms=100)
    out = mgr.get_metrics("plug")[0]
    assert out["slow_count"] == 1
    assert out["call_count"] == 2


def test_record_metric_without_threshold_stays_zero():
    mgr, matcher = _manager_with_matcher()
    mgr.record_metric(matcher, elapsed_ms=999)  # 不传阈值
    out = mgr.get_metrics("plug")[0]
    assert out["slow_count"] == 0


def test_record_metric_threshold_zero_disabled():
    mgr, matcher = _manager_with_matcher()
    mgr.record_metric(matcher, elapsed_ms=999, slow_threshold_ms=0)  # 阈值 0 = 关闭
    out = mgr.get_metrics("plug")[0]
    assert out["slow_count"] == 0
