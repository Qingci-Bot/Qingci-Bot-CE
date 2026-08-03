"""消息日志接口"""

from fastapi import APIRouter, Query, HTTPException, Depends

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth


router = APIRouter()


def _get_bot_instance():
    try:
        return _get_bot()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Bot 未初始化，请先启动 Bot 服务")


@router.get("/messages", dependencies=[Depends(require_auth)])
async def search_messages(
    keyword: str = Query(default=""),
    user_id: int = Query(default=0),
    group_id: int = Query(default=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """搜索消息记录"""
    bot = _get_bot_instance()
    messages = await bot.db.search_messages(
        keyword=keyword,
        user_id=user_id if user_id else None,
        group_id=group_id if group_id else None,
        limit=limit,
        offset=offset,
    )
    return messages


@router.get("/messages/count", dependencies=[Depends(require_auth)])
async def get_message_count():
    """获取消息总数"""
    bot = _get_bot_instance()
    count = await bot.db.get_message_count()
    return {"count": count}


@router.delete("/sessions", dependencies=[Depends(require_auth)])
async def clear_all_sessions():
    """清除所有会话"""
    bot = _get_bot_instance()
    await bot.llm.clear_session()
    return {"message": "所有会话已清除"}