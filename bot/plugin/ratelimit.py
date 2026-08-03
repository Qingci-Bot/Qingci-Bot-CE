"""用户级限流器（内存实现）

功能（对应 RateLimitConfig）：
- 每日上限：单用户当日成功调用次数超过 daily_limit 时拒绝
- 冷却间隔：距上次成功调用不足 cooldown_seconds 时拒绝
- 跨天惰性重置：check 时发现日期变化即重置该用户当日计数
- cleanup(): 清理 7 天未活跃条目（可被定时任务调用，本批手动调用即可）

注意：计数仅在 check 放行时递增，被拒绝的调用不消耗配额。
"""

import time
from datetime import date


class RateLimiter:
    """基于内存 dict 的限流器：{user_id: (日期字符串, 当日计数, 上次成功时间戳)}"""

    def __init__(self, daily_limit: int = 50, cooldown_seconds: int = 10):
        self.daily_limit = daily_limit
        self.cooldown_seconds = cooldown_seconds
        self._data: dict[int, tuple[str, int, float]] = {}

    def check(self, user_id: int) -> tuple[bool, str]:
        """检查用户是否允许本次调用

        Returns:
            (ok, reason): ok 为 True 时放行并计数；为 False 时 reason 为提示文案
        """
        now = time.time()
        today = date.today().isoformat()
        record = self._data.get(user_id)

        # 跨天惰性重置：日期不一致或无记录时，当日计数从 0 开始
        if record is None or record[0] != today:
            count = 0
            last_ts = 0.0
        else:
            count = record[1]
            last_ts = record[2]

        if count >= self.daily_limit:
            return False, f"今日调用次数已达上限（{self.daily_limit} 次），请明天再试。"

        if self.cooldown_seconds > 0 and last_ts and now - last_ts < self.cooldown_seconds:
            return False, f"发送太快啦，请 {self.cooldown_seconds} 秒后再试。"

        # 放行：递增当日计数并记录时间戳（仅成功调用消耗配额）
        self._data[user_id] = (today, count + 1, now)
        return True, ""

    def cleanup(self, inactive_days: int = 7) -> int:
        """清理超过 inactive_days 天未活跃的条目，返回清理条数"""
        cutoff = time.time() - inactive_days * 86400
        stale = [uid for uid, (_, _, ts) in self._data.items() if ts < cutoff]
        for uid in stale:
            del self._data[uid]
        return len(stale)
