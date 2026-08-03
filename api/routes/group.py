"""群粒度配置接口

提供群配置的查询与更新：
- GET  /api/group/list: 列出已配置群（含默认值）
- GET  /api/group/{group_id}: 查询单群配置（未配置时返回默认值）
- PUT  /api/group/{group_id}: 更新群配置（写后失效 chat 插件缓存）
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth
from api.audit import record_audit

logger = logging.getLogger("qingci-bot.api.group")

router = APIRouter()

# 与 config.bot.trigger_mode 保持一致的合法触发模式
_VALID_TRIGGER_MODES = ("always", "at", "keyword")


def _get_bot_or_none():
    try:
        return _get_bot()
    except RuntimeError:
        return None


def _require_bot():
    """获取 Bot 实例，未初始化时返回 503"""
    bot = _get_bot_or_none()
    if bot is None or bot.db is None:
        raise HTTPException(status_code=503, detail="Bot 未初始化")
    return bot


class GroupConfigUpdate(BaseModel):
    """群配置更新请求体（字段均可选，未提供时保留原值）"""
    enabled: Optional[bool] = None
    # None 表示跟随全局；显式传 null 可清除群级覆盖
    trigger_mode: Optional[str] = None


@router.get("/list", dependencies=[Depends(require_auth)])
async def list_group_configs():
    """列出所有已配置的群，并附带默认值（未配置群的行为）"""
    bot = _get_bot_or_none()
    defaults = {
        "enabled": True,
        "trigger_mode": bot.config.bot.trigger_mode if bot else None,
    }
    groups: list[dict] = []
    if bot is not None and bot.db is not None:
        try:
            groups = await bot.db.list_group_configs()
        except Exception:
            logger.exception("查询群配置列表失败")
            raise HTTPException(status_code=500, detail="查询群配置失败，详见服务端日志")
    return {"defaults": defaults, "groups": groups}


@router.get("/{group_id}", dependencies=[Depends(require_auth)])
async def get_group_config(group_id: int):
    """查询单群配置（未配置时返回默认值）"""
    bot = _require_bot()
    try:
        row = await bot.db.get_group_config(group_id)
    except Exception:
        logger.exception(f"查询群配置失败: group_id={group_id}")
        raise HTTPException(status_code=500, detail="查询群配置失败，详见服务端日志")
    if row is None:
        return {
            "group_id": group_id,
            "enabled": True,
            "trigger_mode": None,
            "configured": False,
        }
    row["configured"] = True
    return row


@router.put("/{group_id}", dependencies=[Depends(require_auth)])
async def update_group_config(group_id: int, payload: GroupConfigUpdate, request: Request):
    """更新群配置（未提供的字段保留原值，写后失效 chat 插件缓存）"""
    bot = _require_bot()
    if payload.trigger_mode is not None and payload.trigger_mode not in _VALID_TRIGGER_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"trigger_mode 必须是 {list(_VALID_TRIGGER_MODES)} 之一或 null",
        )
    try:
        existing = await bot.db.get_group_config(group_id) or {}
        enabled = (
            payload.enabled
            if payload.enabled is not None
            else existing.get("enabled", True)
        )
        # 显式传 trigger_mode（含 null）时覆盖；未传时保留原值
        if "trigger_mode" in payload.model_fields_set:
            trigger_mode = payload.trigger_mode
        else:
            trigger_mode = existing.get("trigger_mode")
        await bot.db.upsert_group_config(group_id, enabled, trigger_mode)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"更新群配置失败: group_id={group_id}")
        raise HTTPException(status_code=500, detail="更新群配置失败，详见服务端日志")

    # 失效 chat 插件的群配置缓存，使变更立即生效
    try:
        from bot.plugin.builtin.chat import invalidate_group_config_cache
        invalidate_group_config_cache(group_id)
    except Exception:
        logger.exception("失效群配置缓存失败")

    # 审计埋点：记录群 ID 与生效后的配置摘要（无敏感信息）
    await record_audit(
        "group_config_update",
        f"更新群配置: group_id={group_id}, enabled={enabled}, trigger_mode={trigger_mode}",
        request,
    )

    return {"group_id": group_id, "enabled": enabled, "trigger_mode": trigger_mode}
