"""数据库备份接口

- POST /api/backup/db：使用 sqlite3 官方 backup API 在线备份主库到
  data/backups/ 目录（兼容 WAL 模式），保留最近 10 份并清理更旧备份。
- GET  /api/backup/db/download：下载指定备份文件到浏览器。
- POST /api/backup/restore：上传 .db 备份恢复主库（校验 SQLite 完整性，
  自动先备份当前库，替换前释放连接池）。

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
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from api.audit import record_audit
from api.auth import require_auth
from bot.db.engine import db_path, dispose_engine

logger = logging.getLogger("qingci-bot.api.backup")

router = APIRouter()

# 保留份数
_KEEP_COUNT = 10

# 备份上传体积上限（100 MiB，覆盖大库场景）
_MAX_RESTORE_BYTES = 100 * 1024 * 1024


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


@router.get("/db/download", dependencies=[Depends(require_auth)])
async def download_backup(request: Request, filename: str = ""):
    """下载数据库备份（鉴权 + 文件名白名单防路径穿越）"""
    name = Path(str(filename or "")).name
    if not name.startswith("qingci-bot_") or not name.endswith(".db"):
        raise HTTPException(status_code=400, detail="无效的备份文件名")
    path = _backup_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="备份不存在")
    await record_audit("db_backup_download", f"下载备份: {name}", request)
    return FileResponse(
        str(path),
        media_type="application/octet-stream",
        filename=name,
    )


@router.post("/restore", dependencies=[Depends(require_auth)])
async def restore_db(request: Request):
    """从上传的 .db 备份恢复主库

    流程：multipart 上传 → SQLite 完整性校验 → 自动备份当前库 →
    释放连接池 → 原子替换主库文件。恢复后连接池懒重建，现有会话自动失效。
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_RESTORE_BYTES:
                raise HTTPException(status_code=400, detail="备份文件过大（上限 100 MiB）")
        except (TypeError, ValueError):
            pass
    form = await request.form()
    upload: UploadFile | None = None
    for part in form.values():
        if isinstance(part, UploadFile):
            upload = part
            break
    if upload is None:
        raise HTTPException(status_code=400, detail="缺少备份文件")
    filename = str(getattr(upload, "filename", "") or "").strip()
    if not filename.lower().endswith(".db"):
        raise HTTPException(status_code=400, detail="只支持 .db 数据库备份文件")

    data_dir = db_path().parent
    tmp_path = data_dir / f"restore-upload-{secrets.token_hex(4)}.db"
    try:
        # 边读边写临时文件，避免整文件读入内存
        with tmp_path.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                handle.write(chunk)

        # SQLite 完整性校验：拒绝损坏/伪造文件
        try:
            conn = sqlite3.connect(str(tmp_path))
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise HTTPException(status_code=400, detail="备份文件不是有效的 SQLite 数据库") from exc
        if row is None or str(row[0]) != "ok":
            raise HTTPException(status_code=400, detail="备份文件完整性校验失败")

        # 自动备份当前库（恢复前的最后防线）
        backup_name, _ = await asyncio.to_thread(_do_backup)

        # 释放连接池句柄后原子替换主库文件
        await dispose_engine()
        try:
            await asyncio.to_thread(os.replace, str(tmp_path), str(db_path()))
        except OSError as exc:
            # Windows 下主库文件可能仍被占用（在途连接/只读句柄）
            logger.exception("数据库恢复替换失败")
            raise HTTPException(
                status_code=500,
                detail=f"恢复失败：无法替换主库文件（{exc}）。已保留当前库备份 {backup_name}，请停止 Bot 后手动处理",
            ) from exc

        await record_audit(
            "db_restore", f"恢复数据库: {filename}（原库已备份为 {backup_name}）", request
        )
        return {"success": True, "backup_name": backup_name}
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.warning("清理恢复上传临时文件失败", exc_info=True)
