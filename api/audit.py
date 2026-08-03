"""审计日志：记录与查询

- record_audit(action, detail, request): 异步写 AuditLog，失败仅记日志，
  不影响主链路（埋点端点无需处理异常）
- GET /api/audit/logs: 倒序查询审计日志（require_auth）
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth

logger = logging.getLogger("qingci-bot.api.audit")

router = APIRouter()


async def record_audit(action: str, detail: str, request: Optional[Request] = None) -> None:
    """写入一条审计日志（尽力而为，失败仅记日志）

    Args:
        action: 动作名（如 config_update / bot_start / login）
        detail: 动作摘要（不得包含密钥类信息）
        request: FastAPI 请求对象，用于提取 client_ip
    """
    try:
        client_ip = ""
        if request is not None and request.client is not None:
            client_ip = request.client.host or ""
        # 直接经 session 工厂写入，不依赖 Bot 实例是否已初始化
        from bot.db.engine import get_session_factory
        from bot.db.models import AuditLog
        async with get_session_factory()() as session:
            session.add(AuditLog(action=action, detail=detail, client_ip=client_ip))
            await session.commit()
    except Exception:
        logger.warning(f"写入审计日志失败: action={action}", exc_info=True)


@router.get("/logs", dependencies=[Depends(require_auth)])
async def get_audit_logs(limit: int = Query(default=100, ge=1, le=1000)):
    """审计日志列表（按时间倒序）"""
    try:
        bot = _get_bot()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Bot 未初始化")
    try:
        logs = await bot.db.get_audit_logs(limit=limit)
    except Exception:
        logger.exception("查询审计日志失败")
        raise HTTPException(status_code=500, detail="查询审计日志失败，详见服务端日志")
    return {"logs": logs}
