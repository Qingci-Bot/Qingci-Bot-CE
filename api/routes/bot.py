"""Bot 控制接口"""

from fastapi import APIRouter, HTTPException, Depends

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth


router = APIRouter()


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
async def start_bot():
    """启动 Bot"""
    bot = _get_bot_or_none()
    if not bot:
        raise HTTPException(status_code=503, detail="Bot 未初始化")
    if bot.is_running:
        return {"message": "Bot 已在运行"}
    await bot.start()
    return {"message": "Bot 已启动"}


@router.post("/stop", dependencies=[Depends(require_auth)])
async def stop_bot():
    """停止 Bot"""
    bot = _get_bot_or_none()
    if not bot:
        raise HTTPException(status_code=503, detail="Bot 未初始化")
    if not bot.is_running:
        return {"message": "Bot 未运行"}
    await bot.stop()
    return {"message": "Bot 已停止"}


@router.post("/restart", dependencies=[Depends(require_auth)])
async def restart_bot():
    """重启 Bot"""
    bot = _get_bot_or_none()
    if not bot:
        raise HTTPException(status_code=503, detail="Bot 未初始化")
    if bot.is_running:
        await bot.stop()
    await bot.start()
    return {"message": "Bot 已重启"}


@router.get("/health")
async def health_check():
    """健康检查"""
    bot = _get_bot_or_none()
    return {
        "running": bot.is_running if bot else False,
        "connected": bot.connection.is_connected if bot else False,
    }