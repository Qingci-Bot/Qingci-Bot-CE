"""数据库备份接口

POST /api/backup/db：使用 sqlite3 官方 backup API 在线备份主库到
data/backups/ 目录（兼容 WAL 模式），保留最近 10 份并清理更旧备份。
备份在 asyncio.to_thread 中同步执行，避免阻塞事件循环。
"""

import asyncio
import logging
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from api.audit import record_audit
from api.auth import require_auth
from bot.db.engine import db_path

logger = logging.getLogger("qingci-bot.api.backup")

router = APIRouter()

# 保留份数
_KEEP_COUNT = 10


def _backup_dir() -> Path:
    """备份输出目录（位于当前实例数据根目录下）"""
    return db_path().parent / "backups"


def _cleanup_old_backups() -> None:
    """保留最近 _KEEP_COUNT 份备份，删除更旧的（按文件名时间戳排序）"""
    try:
        files = sorted(_backup_dir().glob("qingci-bot_*.db"), key=lambda p: p.name)
        for old in files[:-_KEEP_COUNT]:
            try:
                old.unlink()
            except OSError:
                logger.warning(f"清理旧备份失败: {old.name}", exc_info=True)
    except Exception:
        logger.warning("清理旧备份目录失败", exc_info=True)


def _do_backup() -> tuple[str, int]:
    """同步执行备份（在线程中调用）：返回 (备份文件名, 文件大小)

    文件名带随机后缀，避免同一秒内并发备份同名互相覆盖；
    清理 glob（qingci-bot_*.db）对新命名同样匹配。
    """
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_path = backup_dir / f"qingci-bot_{timestamp}_{secrets.token_hex(3)}.db"
    # sqlite3 官方 backup API：在线一致性拷贝，兼容 WAL 模式
    src_conn = sqlite3.connect(str(db_path()))
    try:
        dst_conn = sqlite3.connect(str(dst_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    # 备份含对话数据：收紧文件权限（POSIX 0600），防同机其他用户读取
    try:
        os.chmod(dst_path, 0o600)
    except OSError:
        pass
    _cleanup_old_backups()
    return dst_path.name, dst_path.stat().st_size


@router.post("/db", dependencies=[Depends(require_auth)])
async def backup_db(request: Request):
    """备份数据库：返回备份文件名与大小"""
    if not db_path().exists():
        raise HTTPException(status_code=404, detail="数据库文件不存在")
    try:
        filename, size = await asyncio.to_thread(_do_backup)
    except Exception:
        logger.exception("数据库备份失败")
        raise HTTPException(status_code=500, detail="数据库备份失败，详见服务端日志") from None
    # 审计埋点：仅记录文件名与大小，不含任何数据内容
    await record_audit("db_backup", f"数据库备份: {filename} ({size} 字节)", request)
    return {"filename": filename, "size": size}
