"""运行日志采集测试（RunLogHandler / 环形缓冲 / 开关）"""

import logging

from bot.logformat import (
    RunLogHandler,
    clear_runlog,
    get_runlog_new_nowait,
    get_runlog_snapshot,
    set_run_log_enabled,
)


def _make_record(level: int, message: str, name: str = "bot.test") -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=message, args=(), exc_info=None
    )


def test_runlog_handler_emit_collects_snapshot():
    """emit 后快照与"新条目队列"都能取到记录"""
    clear_runlog()
    handler = RunLogHandler()
    handler.emit(_make_record(logging.INFO, "hello 世界"))
    handler.emit(_make_record(logging.WARNING, "warn message"))

    entries = get_runlog_snapshot()
    assert len(entries) == 2
    assert entries[0]["message"] == "hello 世界"
    assert entries[0]["level"] == "INFO"
    assert entries[0]["name"] == "bot.test"
    assert "time" in entries[0]

    # 新条目队列优先消费
    assert get_runlog_new_nowait() is not None
    assert get_runlog_new_nowait() is not None
    assert get_runlog_new_nowait() is None  # 清空后无可消费


def test_runlog_handler_redacts_secrets():
    """运行日志消息应脱敏（防止 API Key / token 泄露到 WebUI）"""
    clear_runlog()
    handler = RunLogHandler()
    handler.emit(_make_record(logging.ERROR, "llm failed url=https://x/y?key=sk-abcdefgh12345&b=1"))

    entry = get_runlog_snapshot()[0]
    assert "sk-abcdefgh12345" not in entry["message"]
    assert "key=***" in entry["message"]


def test_set_run_log_enabled_attaches_and_detaches():
    """set_run_log_enabled 在根 logger 上附加/移除 RunLogHandler"""
    root = logging.getLogger()
    # 从清理状态开始，保证幂等
    set_run_log_enabled(False)
    before = [h for h in root.handlers if isinstance(h, RunLogHandler)]

    set_run_log_enabled(True)
    after_on = [h for h in root.handlers if isinstance(h, RunLogHandler)]
    assert len(after_on) == len(before) + 1

    # 幂等：重复开启不重复附加
    set_run_log_enabled(True)
    after_again = [h for h in root.handlers if isinstance(h, RunLogHandler)]
    assert len(after_again) == len(after_on)

    set_run_log_enabled(False)
    after_off = [h for h in root.handlers if isinstance(h, RunLogHandler)]
    assert len(after_off) == len(before)


def test_runlog_handler_exception_does_not_crash():
    """含异常回溯的记录可收集（exception 字段有值），且进程不中断"""
    clear_runlog()
    handler = RunLogHandler()
    record = logging.LogRecord(
        name="bot.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=(),
        exc_info=(ValueError, ValueError("bad"), None),
    )
    handler.emit(record)
    entries = get_runlog_snapshot()
    assert entries
    assert "valueerror" in entries[0]["exception"].lower()
