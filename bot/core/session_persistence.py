"""会话状态持久化 — 把 SessionStateManager 的 ephemeral 快照落盘

SessionStateManager.serialize()/deserialize() 已提供进程内快照能力，本模块作为
唯一的文件 IO 层接入，保持其纯内存语义不变：

- 保存：serialize() 只导出永不过期（ttl=0）的键，写入 data_root() 下的 JSON 文件，
  先写 .tmp 再 os.replace 原子替换，避免写入中断留下半截文件。
- 恢复：文件缺失视为空；坏文件（非法 JSON / IO / 类型错误）改名 .bak 后退化为
  空启动，绝不阻塞 Bot 启动。
- 并发：模块级 asyncio.Lock 串行化保存，防止周期快照循环与关闭时最终保存并发。

文件位置由 bot/paths.data_root() 决定（镜像 db_path() 的定位方式），
随实例数据目录自包含分发。
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from ..paths import data_root

logger = logging.getLogger("qingci-bot.session_persistence")

_lock = asyncio.Lock()


def session_state_path(path: str | None = None) -> Path:
    """会话快照文件路径（默认 data_root()/session_state.json，可用 path 覆盖）"""
    if path:
        return Path(path).resolve()
    return data_root() / "session_state.json"


async def save_snapshot(manager, path: str | None = None) -> int:
    """保存会话快照，返回写入的会话数

    并发保护：模块级 asyncio.Lock 串行化，防止周期快照与关闭最终保存竞争。
    原子写：写 <path>.tmp 后 os.replace 覆盖。
    """
    data = await manager.serialize()
    target = session_state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    async with _lock:
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, target)
        except Exception:
            logger.exception("保存会话状态快照失败")
            tmp.unlink(missing_ok=True)
            return 0
    return len(data)


async def restore_snapshot(manager, path: str | None = None) -> int:
    """从快照恢复会话状态，返回恢复的键数；任何异常退化为空，不阻塞启动"""
    target = session_state_path(path)
    if not target.exists():
        return 0
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        logger.warning("会话状态快照损坏，已重命名备份后从空状态启动: %s", target)
        bak = target.with_suffix(target.suffix + ".bak")
        try:
            os.replace(target, bak)
        except OSError:
            logger.exception("备份损坏的会话状态快照失败")
        return 0
    if not isinstance(data, dict):
        logger.warning("会话状态快照内容非法（非对象），从空状态启动: %s", target)
        try:
            os.replace(target, target.with_suffix(target.suffix + ".bak"))
        except OSError:
            pass
        return 0
    return await manager.deserialize(data)
