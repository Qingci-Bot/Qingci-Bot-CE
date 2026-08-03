"""错误告警 - ERROR 日志达到阈值时私聊通知管理员

设计要点（对齐批次 1 功能 9）：
- AlertHandler 只处理 ERROR 及以上级别；
- 冷却窗口内（cooldown_minutes 分钟）累计错误达到 error_threshold，
  且距上次告警超过冷却期时才触发告警；
- 告警发送 fire-and-forget：绝不抛异常、绝不回 logging（防止递归触发自身），
  发送失败仅 print 到 stderr；
- 显式忽略本模块告警发送日志（按 logger 名过滤），杜绝自激递归。
"""

import asyncio
import logging
import sys
import time
from collections import deque
from typing import Optional

# 告警发送相关日志专用 logger 名前缀；AlertHandler 忽略该来源的日志防止递归
ALERT_LOGGER_PREFIX = "qingci-bot.alerter"

logger = logging.getLogger(ALERT_LOGGER_PREFIX)

# 告警文案中错误摘要的最大长度（防止超长消息）
_SUMMARY_MAX_LEN = 200


class AlertHandler(logging.Handler):
    """错误告警日志处理器

    挂载到 ``qingci-bot`` 根 logger 后，统计 ERROR 日志；
    达到阈值且冷却期已过时，经注入的 connection 私聊全部管理员。
    """

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self._connection = None          # OneBotConnection（attach 时注入）
        self._admin_users: list[int] = []
        self._threshold: int = 5
        self._cooldown_seconds: float = 600.0
        self._error_times: deque = deque()   # 窗口内错误时间戳
        self._last_summary: str = ""
        self._last_alert_ts: float = 0.0
        self._attached_logger: Optional[logging.Logger] = None

    # ============ 挂载 / 卸载 ============

    def attach(self, target_logger: logging.Logger, connection, config) -> None:
        """挂载到目标 logger（通常为 qingci-bot 根 logger）

        Args:
            target_logger: 被监听的 logger
            connection: OneBotConnection 实例（用于 send_private_msg）
            config: ConfigManager（读取 alert 阈值/冷却与 bot.admin_users）
        """
        self._connection = connection
        self._admin_users = list(config.bot.admin_users or [])
        self._threshold = max(1, int(config.alert.error_threshold))
        self._cooldown_seconds = max(0, int(config.alert.cooldown_minutes)) * 60.0
        self._error_times.clear()
        self._last_alert_ts = 0.0
        self._attached_logger = target_logger
        target_logger.addHandler(self)
        logger.info(
            f"错误告警已启用: threshold={self._threshold}, "
            f"cooldown={config.alert.cooldown_minutes}min, "
            f"admins={len(self._admin_users)}"
        )

    def detach(self) -> None:
        """从目标 logger 卸载（幂等）"""
        if self._attached_logger is not None:
            try:
                self._attached_logger.removeHandler(self)
            except Exception:
                pass
            self._attached_logger = None
        self._connection = None

    # ============ 日志处理 ============

    def emit(self, record: logging.LogRecord) -> None:
        """统计 ERROR 并在满足 阈值+冷却 条件时触发告警

        本方法内部全异常吞掉：告警器绝不能影响业务日志流程。
        """
        try:
            # 防递归：忽略告警器自身来源的日志
            if record.name.startswith(ALERT_LOGGER_PREFIX):
                return
            # 只处理 ERROR 及以上（level=ERROR 已在 Handler 级过滤，这里双保险）
            if record.levelno < logging.ERROR:
                return

            now = time.time()
            self._error_times.append(now)
            # 仅保留冷却窗口内的时间戳（窗口 = 冷却期）
            cutoff = now - self._cooldown_seconds
            while self._error_times and self._error_times[0] < cutoff:
                self._error_times.popleft()

            # 记录最近一条错误摘要（截断防超长）
            self._last_summary = self._summarize(record)

            # 阈值 + 冷却双重判定
            if len(self._error_times) < self._threshold:
                return
            if now - self._last_alert_ts < self._cooldown_seconds:
                return

            # 触发告警：重置窗口计数，进入下一轮冷却
            self._last_alert_ts = now
            count = len(self._error_times)
            self._error_times.clear()
            self._fire_alert(count, self._last_summary)
        except Exception:
            # 绝不抛异常、绝不回 logging（可能递归），仅 stderr
            print("[AlertHandler] 内部处理异常", file=sys.stderr)

    @staticmethod
    def _summarize(record: logging.LogRecord) -> str:
        """生成单条错误摘要（压缩空白 + 截断）"""
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            msg = f"{type(exc).__name__}: {msg}"
        msg = " ".join(str(msg).split())
        if len(msg) > _SUMMARY_MAX_LEN:
            msg = msg[:_SUMMARY_MAX_LEN] + "..."
        return msg

    # ============ 告警发送（fire-and-forget） ============

    def _fire_alert(self, count: int, summary: str) -> None:
        """向全部管理员私聊发送告警文案"""
        if self._connection is None or not self._admin_users:
            return
        text = f"[Qingci-Bot 错误告警] 近期累计 {count} 条 ERROR，最近一条: {summary}"
        for user_id in self._admin_users:
            self._send_private(user_id, text)

    def _send_private(self, user_id: int, text: str) -> None:
        """fire-and-forget 发送：任何失败仅 print stderr，绝不抛出、不回 logging"""
        try:
            coro = self._connection.send_private_msg(user_id, text)
        except Exception as e:
            print(f"[AlertHandler] 构造告警消息失败 user={user_id}: {e}", file=sys.stderr)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        try:
            if loop is not None and loop.is_running():
                # 常规路径：事件循环内调度后台任务
                loop.create_task(self._guarded_send(coro, user_id))
            else:
                # 非事件循环线程触发日志（罕见）：尝试独立运行一次
                asyncio.run(coro)
        except Exception as e:
            print(f"[AlertHandler] 告警发送调度失败 user={user_id}: {e}", file=sys.stderr)

    @staticmethod
    async def _guarded_send(coro, user_id: int) -> None:
        """后台任务体：吞掉发送异常，仅 stderr 输出"""
        try:
            await coro
        except Exception as e:
            print(f"[AlertHandler] 告警发送失败 user={user_id}: {e}", file=sys.stderr)
