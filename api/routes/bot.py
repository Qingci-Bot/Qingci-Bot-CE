"""Bot 控制接口"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth
from api.audit import record_audit

logger = logging.getLogger("qingci-bot.api.bot")

router = APIRouter()

# 生命周期锁：惰性创建，避免模块导入时绑定到（可能不同的）事件循环
_lifecycle_lock: Optional[asyncio.Lock] = None

# 启动/停止超时保护（秒）：stop 内部最长约 7s，使用较短超时
_START_TIMEOUT = 30
_STOP_TIMEOUT = 15


def _get_lifecycle_lock() -> asyncio.Lock:
    """获取生命周期锁（在当前事件循环中惰性创建）"""
    global _lifecycle_lock
    if _lifecycle_lock is None:
        _lifecycle_lock = asyncio.Lock()
    return _lifecycle_lock


async def _cleanup_after_timeout(bot) -> None:
    """超时兜底清理：尽力调用一次 bot.stop()，回收部分启动/停止的残留资源

    wait_for 超时会向 bot.start()/bot.stop() 注入 CancelledError，可能残留
    已加载的插件/已连接的 DB 等资源；此处用短超时包裹并忽略自身异常，
    最终状态以 /status 为准。
    """
    try:
        await asyncio.wait_for(bot.stop(), timeout=_STOP_TIMEOUT)
    except Exception:
        logger.exception("超时后兜底清理失败")


def _get_bot_or_none():
    try:
        return _get_bot()
    except RuntimeError:
        return None


@router.get("/status")
async def get_status():
    """获取 Bot 运行状态"""
    bot = _get_bot_or_none()
    if bot:
        return bot.get_status()
    return {"running": False, "connected": False, "plugins": []}


@router.post("/start", dependencies=[Depends(require_auth)])
async def start_bot(request: Request):
    """启动 Bot"""
    async with _get_lifecycle_lock():
        bot = _get_bot_or_none()
        if not bot:
            raise HTTPException(status_code=503, detail="Bot 未初始化")
        if bot.is_running:
            return {"message": "Bot 已在运行"}
        try:
            await asyncio.wait_for(bot.start(), timeout=_START_TIMEOUT)
            await record_audit("bot_start", "启动 Bot", request)
            return {"message": "Bot 启动成功"}
        except asyncio.TimeoutError:
            logger.error(f"Bot 启动超时（>{_START_TIMEOUT}s），执行兜底清理")
            await _cleanup_after_timeout(bot)
            raise HTTPException(
                status_code=504,
                detail="Bot 启动超时，已尝试清理，请通过 /status 确认状态",
            )
        except Exception:
            logger.exception("Bot 启动失败")
            raise HTTPException(status_code=500, detail="启动失败，详见服务端日志")


@router.post("/stop", dependencies=[Depends(require_auth)])
async def stop_bot(request: Request):
    """停止 Bot"""
    async with _get_lifecycle_lock():
        bot = _get_bot_or_none()
        if not bot:
            raise HTTPException(status_code=503, detail="Bot 未初始化")
        if not bot.is_running:
            return {"message": "Bot 未在运行"}
        try:
            await asyncio.wait_for(bot.stop(), timeout=_STOP_TIMEOUT)
            await record_audit("bot_stop", "停止 Bot", request)
            return {"message": "Bot 停止成功"}
        except asyncio.TimeoutError:
            logger.error(f"Bot 停止超时（>{_STOP_TIMEOUT}s），执行兜底清理")
            await _cleanup_after_timeout(bot)
            raise HTTPException(
                status_code=504,
                detail="Bot 停止超时，已尝试清理，请通过 /status 确认状态",
            )
        except Exception:
            logger.exception("Bot 停止失败")
            raise HTTPException(status_code=500, detail="停止失败，详见服务端日志")


@router.post("/restart", dependencies=[Depends(require_auth)])
async def restart_bot(request: Request):
    """重启 Bot"""
    async with _get_lifecycle_lock():
        bot = _get_bot_or_none()
        if not bot:
            raise HTTPException(status_code=503, detail="Bot 未初始化")
        stopped = False
        try:
            if bot.is_running:
                try:
                    await asyncio.wait_for(bot.stop(), timeout=_STOP_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error(f"重启时 Bot 停止超时（>{_STOP_TIMEOUT}s），执行兜底清理")
                    await _cleanup_after_timeout(bot)
                    raise HTTPException(
                        status_code=504,
                        detail="重启超时（停止阶段），已尝试清理，请通过 /status 确认状态",
                    )
                stopped = True
            try:
                await asyncio.wait_for(bot.start(), timeout=_START_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(f"重启时 Bot 启动超时（>{_START_TIMEOUT}s），执行兜底清理")
                await _cleanup_after_timeout(bot)
                detail = (
                    "重启超时，Bot 处于部分启动状态，已尝试清理，请通过 /status 确认状态"
                    if stopped
                    else "Bot 启动超时，已尝试清理，请通过 /status 确认状态"
                )
                raise HTTPException(status_code=504, detail=detail)
            await record_audit("bot_restart", "重启 Bot", request)
            return {"message": "Bot 重启成功"}
        except HTTPException:
            raise
        except Exception:
            if stopped:
                logger.exception("Bot 已停止且重启失败")
                raise HTTPException(
                    status_code=500, detail="Bot 已停止且重启失败，详见服务端日志"
                )
            logger.exception("Bot 重启失败")
            raise HTTPException(status_code=500, detail="重启失败，详见服务端日志")


@router.get("/health")
async def health_check():
    """健康检查"""
    bot = _get_bot_or_none()
    return {
        "running": bot.is_running if bot else False,
        "connected": bot.connection.is_connected if bot else False,
    }