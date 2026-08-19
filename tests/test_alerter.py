"""错误告警模块测试

聚焦 AlertHandler 的阈值 + 冷却判定逻辑与摘要生成；
告警发送为 fire-and-forget，测试通过 mock _fire_alert 验证触发条件。
"""

import logging
from types import SimpleNamespace

from bot.alerter import AlertHandler


def _config(threshold=2, cooldown=1, admins=(), super_admin=None):
    """构造最小配置对象"""
    return SimpleNamespace(
        bot=SimpleNamespace(
            admin_users=list(admins),
            super_admin=super_admin,
        ),
        alert=SimpleNamespace(
            error_threshold=threshold,
            cooldown_minutes=cooldown,
        ),
    )


def _record(msg="boom", level=logging.ERROR, name="qingci-bot.test"):
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_attach_reads_threshold_cooldown_admins():
    h = AlertHandler()
    target = logging.getLogger(f"test-alerter-attach-{id(h)}")
    conn = object()
    h.attach(target, conn, _config(threshold=3, cooldown=5, admins=[111]))
    assert h._threshold == 3
    assert h._cooldown_seconds == 300.0
    assert h._admin_users == [111]
    assert h._connection is conn
    assert h in target.handlers
    h.detach()
    assert h not in target.handlers


def test_attach_includes_super_admin():
    h = AlertHandler()
    target = logging.getLogger(f"test-alerter-super-{id(h)}")
    h.attach(target, object(), _config(admins=[111], super_admin=222))
    assert 111 in h._admin_users
    assert 222 in h._admin_users
    h.detach()


def test_attach_dedup_super_admin():
    h = AlertHandler()
    target = logging.getLogger(f"test-alerter-dedup-{id(h)}")
    h.attach(target, object(), _config(admins=[111], super_admin=111))
    assert h._admin_users == [111]
    h.detach()


def test_below_threshold_no_alert(monkeypatch):
    h = AlertHandler()
    h.attach(logging.getLogger(f"test-alerter-low-{id(h)}"), object(), _config(threshold=3))
    fired = []
    monkeypatch.setattr(h, "_fire_alert", lambda count, summary: fired.append(count))
    h.emit(_record())
    h.emit(_record())
    assert fired == []
    h.detach()


def test_reaches_threshold_fires_alert(monkeypatch):
    h = AlertHandler()
    h.attach(logging.getLogger(f"test-alerter-hit-{id(h)}"), object(), _config(threshold=2))
    fired = []
    monkeypatch.setattr(h, "_fire_alert", lambda count, summary: fired.append((count, summary)))
    h.emit(_record("first error"))
    assert fired == []
    h.emit(_record("second error"))
    assert len(fired) == 1
    assert fired[0][0] == 2
    assert "second error" in fired[0][1]
    h.detach()


def test_cooldown_suppresses_immediate_retrigger(monkeypatch):
    h = AlertHandler()
    h.attach(logging.getLogger(f"test-alerter-cool-{id(h)}"), object(), _config(threshold=2))
    fired = []
    monkeypatch.setattr(h, "_fire_alert", lambda count, summary: fired.append(count))
    # 达到阈值触发
    h.emit(_record())
    h.emit(_record())
    assert len(fired) == 1
    # 冷却期内再次累计到阈值，不重复触发
    h.emit(_record())
    h.emit(_record())
    assert len(fired) == 1
    h.detach()


def test_ignores_warning_level(monkeypatch):
    h = AlertHandler()
    h.attach(logging.getLogger(f"test-alerter-warn-{id(h)}"), object(), _config(threshold=1))
    fired = []
    monkeypatch.setattr(h, "_fire_alert", lambda count, summary: fired.append(count))
    h.emit(_record(level=logging.WARNING))
    assert fired == []
    h.detach()


def test_ignores_alerter_own_logs(monkeypatch):
    h = AlertHandler()
    h.attach(logging.getLogger(f"test-alerter-self-{id(h)}"), object(), _config(threshold=1))
    fired = []
    monkeypatch.setattr(h, "_fire_alert", lambda count, summary: fired.append(count))
    h.emit(_record(name="qingci-bot.alerter"))
    assert fired == []
    h.detach()


def test_emit_never_raises(monkeypatch):
    h = AlertHandler()
    h.attach(logging.getLogger(f"test-alerter-safe-{id(h)}"), object(), _config(threshold=1))

    # _fire_alert 抛异常也不影响 emit
    def _boom(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(h, "_fire_alert", _boom)
    h.emit(_record())  # 不应抛出
    h.detach()


def test_summarize_compresses_whitespace():
    h = AlertHandler()
    summary = h._summarize(_record("a\n   b   c"))
    assert summary == "a b c"


def test_summarize_truncates_long():
    h = AlertHandler()
    long_msg = "x" * 500
    summary = h._summarize(_record(long_msg))
    assert len(summary) == 200 + 3  # 截断到 200 字符 + "..."
    assert summary.endswith("...")


def test_summarize_includes_exception_type():
    h = AlertHandler()
    try:
        raise ValueError("bad value")
    except ValueError as exc:
        record = logging.LogRecord(
            name="qingci-bot",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="context",
            args=(),
            exc_info=(ValueError, exc, __import__("sys").exc_info()[2]),
        )
    summary = h._summarize(record)
    assert "ValueError" in summary
