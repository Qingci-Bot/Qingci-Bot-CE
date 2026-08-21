"""结构化日志 - JSON Formatter + 日志轮转 + 模块级 Logger 配置

特性：
- JSON 格式输出（config.bot.log_json=True 时切换）
- 日志轮转：按文件大小轮转，保留最近 N 个备份（config.log 节配置）
- 模块级 Logger：按需获取命名 logger，支持独立日志级别
- 启动时由 main.py / desktop/main.py 调用 apply_logging_from_config 统一配置
"""

import collections
import contextvars
import datetime
import json
import logging
import logging.handlers
import queue
import threading
from pathlib import Path
from typing import Union

# 事件链路追踪：contextvar 贯穿单条事件处理链路，跨模块串查同一条消息的
# 分发/匹配/插件/LLM 处理过程（排障"这条消息为什么没回复"）。
# 每条事件在独立 asyncio task 中处理，task 自带独立 context，无需手动复位。
event_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("event_id", default="")


def set_event_id(event_id: str) -> None:
    """为当前事件处理链路设置 event_id（空值/纯空白忽略，保持默认）"""
    if event_id and event_id.strip():
        event_id_var.set(event_id)


class TraceFilter(logging.Filter):
    """把当前链路的 event_id 附加到每条日志记录（无 id 时为空字符串）"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.event_id = event_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志：time / level / name / message（异常时附 exception 字段）"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        event_id = getattr(record, "event_id", "")
        if event_id:
            payload["event_id"] = event_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            # message 含不可序列化对象时兜底为 repr，保证仍是合法 JSON
            payload["message"] = repr(record.msg)
            return json.dumps(payload, ensure_ascii=False)


# ── 运行日志（环形缓冲 + WS 实时推送）────────────────────────
#
# 把框架运行日志（WARNING/INFO/ERROR/Debug 等）采集到内存环形缓冲，
# 供 WebUI 侧边栏"运行日志"页实时查看与排障。日志由任意线程写入，
# 故缓冲与"新条目队列"均为线程安全；WS 层（api/server.py）从新条目队列
# 消费并推送给前端，同时可发送快照（环形缓冲内容）供新连接初始化。

# 环形缓冲上限：超过的旧日志被丢弃，避免长期运行内存无限膨胀
_RUNLOG_MAX_ENTRIES = 1000
# 新条目队列上限：WS 端未消费时丢弃最旧日志（低频日志场景可缓存足够多）
_RUNLOG_FEED_MAX = 2000


class _RunLogStore:
    """线程安全的运行日志存储（环形缓冲 + 新条目队列）"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: collections.deque[dict] = collections.deque(maxlen=_RUNLOG_MAX_ENTRIES)
        self._feed: queue.Queue[dict] = queue.Queue(maxsize=_RUNLOG_FEED_MAX)

    def push(self, entry: dict) -> None:
        with self._lock:
            self._buffer.append(entry)
        try:
            self._feed.put_nowait(entry)
        except queue.Full:
            pass  # WS 端积压过多人手不足时丢弃，环形缓冲仍保留快照

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
        try:
            while True:
                self._feed.get_nowait()
        except queue.Empty:
            pass

    def get_new_nowait(self) -> dict | None:
        """非阻塞取一条新条目（无则返回 None）——由 WS 后台泵循环调用"""
        try:
            return self._feed.get_nowait()
        except queue.Empty:
            return None


_runlog_store = _RunLogStore()

# 运行日志 Handler 单例（attach/detach 由 configure_logging / set_run_log_enabled 控制）
_runlog_handler: "RunLogHandler | None" = None
_runlog_handler_lock = threading.Lock()


def get_runlog_snapshot() -> list[dict]:
    """获取运行日志快照（环形缓冲内容，供 WS 新连接初始化）"""
    return _runlog_store.snapshot()


def get_runlog_new_nowait() -> dict | None:
    """非阻塞取一条新运行日志（无则返回 None）——由 WS 后台泵循环调用"""
    return _runlog_store.get_new_nowait()


def clear_runlog() -> None:
    """清空运行日志缓冲（前端"清屏"按钮调用）"""
    _runlog_store.clear()


def _redact_runlog_message(entry: dict) -> dict:
    """对日志消息做 URL/密钥脱敏，防止运行日志页面泄露敏感信息"""
    try:
        from .logredact import redact_secrets

        entry["message"] = redact_secrets(str(entry.get("message", "")))
    except Exception:
        pass
    return entry


class RunLogHandler(logging.Handler):
    """运行日志 Handler：把日志落到环形缓冲并投递给新条目队列

    只记录达到配置级别的日志（级别由 config.log.level 控制）；DEBUG 级别
    日志仅在开启 DEBUG 时才会进入运行日志页，避免信息过载。
    """

    def __init__(self) -> None:
        super().__init__()
        self.addFilter(TraceFilter())
        # 复用一个基础 Formatter，仅用于格式化异常回溯（Handler 本身无 formatException）
        self._formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "time": datetime.datetime.fromtimestamp(record.created).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            }
            event_id = getattr(record, "event_id", "")
            if event_id:
                entry["event_id"] = event_id
            if record.exc_info:
                entry["exception"] = self._formatter.formatException(record.exc_info)
            _runlog_store.push(_redact_runlog_message(entry))
        except Exception:
            self.handleError(record)


def _attach_runlog_handler(root_logger: logging.Logger) -> None:
    global _runlog_handler
    with _runlog_handler_lock:
        if _runlog_handler is None:
            _runlog_handler = RunLogHandler()
        if _runlog_handler not in root_logger.handlers:
            root_logger.addHandler(_runlog_handler)


def _detach_runlog_handler(root_logger: logging.Logger) -> None:
    global _runlog_handler
    with _runlog_handler_lock:
        if _runlog_handler is not None and _runlog_handler in root_logger.handlers:
            root_logger.removeHandler(_runlog_handler)


def set_run_log_enabled(enabled: bool) -> None:
    """动态开关运行日志采集（配合设置页 `log.run_log_enabled` 即时生效）"""
    root_logger = logging.getLogger()
    if enabled:
        _attach_runlog_handler(root_logger)
    else:
        _detach_runlog_handler(root_logger)
        # 关闭采集时清空已缓冲的旧日志，兑现"关闭后运行日志页无数据"的承诺
        _runlog_store.clear()


# ── 日志轮转 ──────────────────────────────────────────────────


def _resolve_log_dir(config: object) -> Path:
    """解析日志目录（相对路径基于可写数据根 data_root）"""
    log_dir = getattr(config, "log_dir", None)
    if log_dir and isinstance(log_dir, str):
        p = Path(log_dir)
    else:
        p = Path("logs")
    if not p.is_absolute():
        from .paths import data_root

        p = data_root() / p
    return p


def _add_rotating_file_handler(
    root_logger: logging.Logger,
    log_dir: Path,
    config: object,
    formatter: logging.Formatter,
) -> None:
    """添加日志轮转文件处理器

    配置项（config.log 节）：
    - log_file_enabled: 是否启用文件日志（默认 False 保持向后兼容）
    - log_file_max_bytes: 单文件最大字节数（默认 10 MB）
    - log_file_backup_count: 保留备份数（默认 5）
    """
    enabled = getattr(config, "log_file_enabled", None)
    if not enabled:
        return
    max_bytes = max(1024, int(getattr(config, "log_file_max_bytes", 0) or 0) or 10 * 1024 * 1024)
    backup_count = max(1, int(getattr(config, "log_file_backup_count", 0) or 0) or 5)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    handler = logging.handlers.RotatingFileHandler(
        filename=str(log_dir / "qingci-bot.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    handler.addFilter(TraceFilter())
    root_logger.addHandler(handler)


# ── 入口配置 ──────────────────────────────────────────────────


def configure_logging(
    log_json: bool = False,
    log_level: str = "INFO",
    file_config: object | None = None,
    run_log_enabled: bool = False,
) -> None:
    """按开关配置根日志

    Args:
        log_json: True 切换 JSON 格式
        log_level: 日志级别（DEBUG/INFO/WARNING/ERROR）
        file_config: 日志文件配置（config.log 节），非 None 时启用文件轮转
        run_log_enabled: 是否启用运行日志采集（config.log.run_log_enabled）
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = (
        JsonFormatter()
        if log_json
        else logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    handler.addFilter(TraceFilter())
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # 移除所有现有 handler 后重新添加（force=True 等价效果）
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    root_logger.addHandler(handler)

    # 文件轮转
    if file_config is not None:
        _add_rotating_file_handler(root_logger, _resolve_log_dir(file_config), file_config, fmt)

    # 运行日志采集（环形缓冲 + WS 推送）；默认关闭，由 config.log.run_log_enabled 控制
    set_run_log_enabled(run_log_enabled)


def apply_logging_from_config(config_path: Union[str, "object"]) -> None:
    """读取 config 并配置日志（JSON 格式 + 文件轮转）

    任何异常（配置文件缺失/损坏等）均静默忽略，保持默认文本日志，
    绝不影响启动流程。
    """
    try:
        from pathlib import Path

        from .config import ConfigManager

        cfg = ConfigManager(Path(str(config_path)))
        loaded = cfg.load()
        log_json = loaded.bot.log_json
        log_level = getattr(loaded.log, "level", "INFO")
        file_config = loaded.log if getattr(loaded.log, "log_file_enabled", False) else None
        run_log_enabled = getattr(loaded.log, "run_log_enabled", True)
        configure_logging(
            log_json=log_json,
            log_level=log_level,
            file_config=file_config,
            run_log_enabled=run_log_enabled,
        )
    except Exception:
        pass


# ── 模块级 Logger 工具 ────────────────────────────────────────


def get_module_logger(name: str) -> logging.Logger:
    """获取模块级 Logger（命名空间前缀 qingci-bot）

    用法：
        from bot.logformat import get_module_logger
        logger = get_module_logger(__name__)
    """
    return logging.getLogger(name)
