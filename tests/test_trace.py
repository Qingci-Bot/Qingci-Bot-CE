"""事件链路追踪（event_id）测试

验证 set_event_id 注入的 event_id 能随 JSON 结构化日志输出，
且未设置时日志不包含 event_id 字段（默认行为不变）。
"""

import json
import logging


def _capture(root: logging.Logger, fn) -> str:
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    from bot.logformat import JsonFormatter, TraceFilter

    handler.setFormatter(JsonFormatter())
    handler.addFilter(TraceFilter())
    root.addHandler(handler)
    try:
        fn()
    finally:
        root.removeHandler(handler)
    return stream.getvalue().strip()


def test_event_id_in_json_log(tmp_path):
    from bot.logformat import set_event_id

    logger = logging.getLogger("qingci-bot.test.trace")
    logger.setLevel(logging.INFO)

    # 未设置 event_id：JSON 不含该字段
    out = _capture(logger, lambda: logger.info("no trace"))
    payload = json.loads(out)
    assert "event_id" not in payload

    # 设置 event_id 后：JSON 携带该字段
    def _emit():
        set_event_id("evt-42")
        logger.info("with trace")

    payload = json.loads(_capture(logger, _emit))
    assert payload["event_id"] == "evt-42"


def test_set_event_id_ignores_empty():
    from bot.logformat import event_id_var, set_event_id

    event_id_var.set("keep-me")
    set_event_id("")
    assert event_id_var.get() == "keep-me"
    set_event_id("  ")
    assert event_id_var.get() == "keep-me"
    set_event_id("abc")
    assert event_id_var.get() == "abc"
