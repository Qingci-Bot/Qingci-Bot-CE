"""结构化日志 - JSON Formatter（零新依赖，约 20 行核心实现）

仅当 config.bot.log_json=True 时由入口（main.py / desktop/main.py）切换为
JSON 输出；False 时不触碰现有 logging 配置，文本格式完全不变。
"""

import datetime
import json
import logging
from typing import Union


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志：time / level / name / message（异常时附 exception 字段）"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.datetime.fromtimestamp(record.created)
            .strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            # message 含不可序列化对象时兜底为 repr，保证仍是合法 JSON
            payload["message"] = repr(record.msg)
            return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_json: bool = False) -> None:
    """按开关配置根日志：True 切换 JSON 格式；False 保持现有配置不变"""
    if not log_json:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True,  # 覆盖入口默认的文本 handler
    )


def apply_logging_from_config(config_path: Union[str, "object"]) -> None:
    """读取 config.bot.log_json 并切换日志格式

    任何异常（配置文件缺失/损坏等）均静默忽略，保持默认文本日志，
    绝不影响启动流程。
    """
    try:
        from pathlib import Path

        from ..config import ConfigManager

        cfg = ConfigManager(Path(str(config_path)))
        configure_logging(cfg.load().bot.log_json)
    except Exception:
        pass
