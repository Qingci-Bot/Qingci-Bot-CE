"""用户级限流器单元测试"""

from bot.plugin.ratelimit import RateLimiter


def test_allow_within_daily_limit():
    rl = RateLimiter(daily_limit=3, cooldown_seconds=0)
    assert rl.check(1) == (True, "")
    assert rl.check(1) == (True, "")
    assert rl.check(1) == (True, "")
    # 达到每日上限后拒绝
    ok, reason = rl.check(1)
    assert ok is False
    assert "上限" in reason


def test_cooldown_blocks_recent_calls():
    rl = RateLimiter(daily_limit=100, cooldown_seconds=10)
    assert rl.check(1) == (True, "")
    # 冷却期内拒绝
    ok, reason = rl.check(1)
    assert ok is False
    assert "太快" in reason


def test_cooldown_expires(monkeypatch):
    rl = RateLimiter(daily_limit=100, cooldown_seconds=10)
    now = [1000.0]
    monkeypatch.setattr("bot.plugin.ratelimit.time.time", lambda: now[0])
    rl.check(1)
    # 冷却期内拒绝
    assert rl.check(1)[0] is False
    # 冷却结束前最后一次探测
    now[0] += 9.9
    assert rl.check(1)[0] is False
    # 冷却结束后放行
    now[0] += 0.2
    assert rl.check(1)[0] is True


def test_rejected_does_not_consume_quota(monkeypatch):
    rl = RateLimiter(daily_limit=2, cooldown_seconds=10)
    now = [1000.0]
    monkeypatch.setattr("bot.plugin.ratelimit.time.time", lambda: now[0])
    assert rl.check(1)[0] is True  # 第 1 次放行
    now[0] += 20  # 越过冷却期
    assert rl.check(1)[0] is True  # 第 2 次放行（达每日上限 2）
    now[0] += 20
    assert rl.check(1)[0] is False  # 配额已尽
    # 被拒绝的调用不消耗配额，也不会重置冷却时间戳


def test_reset_on_new_day(monkeypatch):
    rl = RateLimiter(daily_limit=2, cooldown_seconds=0)
    # ratelimit 模块内是 `from datetime import date`，需 monkeypatch 模块内绑定
    import datetime

    real_date = datetime.date

    class _FakeDate(real_date):
        _d = real_date(2026, 8, 16)

        @classmethod
        def today(cls):
            return cls._d

    monkeypatch.setattr("bot.plugin.ratelimit.date", _FakeDate)
    assert rl.check(1)[0] is True
    assert rl.check(1)[0] is True
    assert rl.check(1)[0] is False  # 当日上限

    # 跨天：重置计数
    _FakeDate._d = real_date(2026, 8, 17)
    assert rl.check(1)[0] is True


def test_cleanup_removes_stale(monkeypatch):
    rl = RateLimiter(daily_limit=100, cooldown_seconds=0)
    now = [1000.0]
    monkeypatch.setattr("bot.plugin.ratelimit.time.time", lambda: now[0])
    rl.check(1)
    rl.check(2)
    assert rl.cleanup(inactive_days=1) == 0  # 刚活跃，不清理
    # 快进 2 天
    now[0] += 2 * 86400
    assert rl.cleanup(inactive_days=1) == 2
    assert rl.cleanup(inactive_days=1) == 0
