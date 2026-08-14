"""消息日志接口

含用量统计（/usage）与消息 CSV 流式导出（/messages/export）。
"""

import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api.audit import record_audit
from api.auth import require_auth
from bot.core.bot import get_bot as _get_bot

logger = logging.getLogger("qingci-bot.api.log")


router = APIRouter()

# CSV 导出列与分批大小（id 游标分页，避免一次性载入全表）
_EXPORT_COLUMNS = (
    "message_id",
    "user_id",
    "group_id",
    "content",
    "message_type",
    "role",
    "created_at",
)
_EXPORT_BATCH_SIZE = 1000


def _get_bot_instance():
    try:
        return _get_bot()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Bot 未初始化，请先启动 Bot 服务") from None


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


@router.delete("/messages", dependencies=[Depends(require_auth)])
async def clear_messages(
    request: Request,
    user_id: int = Query(default=0),
    group_id: int = Query(default=0),
    before_days: int = Query(default=0, ge=0),
    confirm: bool = Query(default=False),
):
    """清理消息记录

    - user_id: 仅清理该用户的消息
    - group_id: 仅清理该群的消息
    - before_days: 仅清理 N 天前的消息

    所有参数为 0 或不传时表示清理全部消息，需显式传入 confirm=true 确认。
    """
    bot = _get_bot_instance()
    kwargs: dict = {}
    if user_id:
        kwargs["user_id"] = user_id
    if group_id:
        kwargs["group_id"] = group_id
    if before_days:
        kwargs["before_days"] = before_days
    # 无任何过滤参数即清理全部消息，需显式确认；带过滤的部分删除不受影响
    if not kwargs and not confirm:
        raise HTTPException(
            status_code=400,
            detail="清理全部消息需显式传入 confirm=true 确认",
        )
    cond = ", ".join(f"{k}={v}" for k, v in kwargs.items()) or "全部"
    await record_audit("clear_messages", f"清理消息记录 ({cond})", request)
    count = await bot.db.clear_messages(**kwargs)
    return {"message": f"已清理 {count} 条消息记录", "count": count}


@router.get("/usage", dependencies=[Depends(require_auth)])
async def get_usage_stats(days: int = Query(default=30, ge=1, le=365)):
    """用量统计：按天聚合最近 days 天的 token 用量与调用次数

    返回：daily 数组（date/prompt_tokens/completion_tokens/calls/total_tokens，
    按日期升序）+ summary 汇总。
    """
    bot = _get_bot_instance()
    try:
        daily = await bot.db.get_usage_stats(days=days)
    except Exception:
        raise HTTPException(status_code=500, detail="查询用量统计失败，详见服务端日志") from None
    summary = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "total_tokens": 0}
    for row in daily:
        row["total_tokens"] = row["prompt_tokens"] + row["completion_tokens"]
        summary["prompt_tokens"] += row["prompt_tokens"]
        summary["completion_tokens"] += row["completion_tokens"]
        summary["calls"] += row["calls"]
    summary["total_tokens"] = summary["prompt_tokens"] + summary["completion_tokens"]
    return {"days": days, "daily": daily, "summary": summary}


@router.get("/messages/export", dependencies=[Depends(require_auth)])
async def export_messages():
    """流式导出全部消息为 CSV

    - utf-8-sig 头（Excel 直接打开不乱码）
    - 按 id 游标分批（每批 1000 行），内存占用恒定
    """
    bot = _get_bot_instance()

    async def _generate():
        # utf-8-sig BOM，保证 Excel 识别 UTF-8
        yield "\ufeff"
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(_EXPORT_COLUMNS)
        yield buf.getvalue()
        after_id = 0
        while True:
            try:
                rows = await bot.db.get_messages_batch(after_id=after_id, limit=_EXPORT_BATCH_SIZE)
            except Exception:
                # 流已开始输出时无法再改状态码，仅中止并记日志
                logger.exception("导出消息分批查询失败")
                break
            if not rows:
                break
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="\n")
            for row in rows:
                writer.writerow([row.get(col, "") for col in _EXPORT_COLUMNS])
            yield buf.getvalue()
            after_id = rows[-1]["id"]
            if len(rows) < _EXPORT_BATCH_SIZE:
                break

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=messages.csv"},
    )


@router.delete("/sessions", dependencies=[Depends(require_auth)])
async def clear_all_sessions(confirm: bool = Query(default=False)):
    """清除所有会话（需显式传入 confirm=true 确认）"""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="清除所有会话需显式传入 confirm=true 确认",
        )
    bot = _get_bot_instance()
    await bot.llm.clear_session()
    return {"message": "所有会话已清除"}


# ============ 会话历史可视化 ============


@router.get("/sessions", dependencies=[Depends(require_auth)])
async def list_sessions():
    """列出所有 LLM 会话（按最后活跃时间倒序）"""
    bot = _get_bot_instance()
    try:
        sessions = await bot.db.list_sessions()
    except Exception:
        logger.exception("查询会话列表失败")
        raise HTTPException(status_code=500, detail="查询会话列表失败，详见服务端日志") from None
    return {"sessions": sessions}


@router.get("/sessions/messages", dependencies=[Depends(require_auth)])
async def get_session_messages(
    key: str = Query(..., min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
):
    """查看指定会话的消息历史（key 格式：private:{uid} / group:{gid}:{uid}）"""
    bot = _get_bot_instance()
    try:
        messages = await bot.db.get_sessions(key, limit=limit)
    except Exception:
        logger.exception("查询会话消息失败")
        raise HTTPException(status_code=500, detail="查询会话消息失败，详见服务端日志") from None
    return {"session_key": key, "messages": messages}


@router.delete("/sessions/one", dependencies=[Depends(require_auth)])
async def delete_session(key: str = Query(..., min_length=1), request: Request = None) -> dict:  # type: ignore[assignment]  # FastAPI 自动注入请求对象，用于审计日志提取 client_ip
    """删除指定会话（清空其历史）

    必须经 LLMManager 清除：仅删 DB 会遗留内存缓存，
    导致下次对话继续使用"已删除"的历史（历史复活）。
    """
    bot = _get_bot_instance()
    try:
        if bot.llm is not None:
            await bot.llm.clear_session_by_key(key)
        else:
            await bot.db.clear_sessions(session_key=key)
    except Exception:
        logger.exception("删除会话失败")
        raise HTTPException(status_code=500, detail="删除会话失败，详见服务端日志") from None
    await record_audit("delete_session", f"删除会话 {key}", request)
    return {"message": f"会话 {key} 已删除"}
