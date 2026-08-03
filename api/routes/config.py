"""配置管理接口"""

from fastapi import APIRouter, HTTPException, Depends

from bot.core.bot import get_bot as _get_bot
from bot.config import ConfigManager
from api.auth import require_auth


def _get_config_manager() -> ConfigManager:
    """获取 Bot 实例的 ConfigManager，若 Bot 未初始化则使用默认路径"""
    try:
        bot = _get_bot()
        return bot.config
    except RuntimeError:
        cfg = ConfigManager()
        cfg.load()
        return cfg


async def _maybe_notify_bot():
    """如果 Bot 已运行，通知其重新加载配置"""
    try:
        bot = _get_bot()
        if bot and bot.is_running:
            bot.config.reload()
            await bot.llm.reload(bot.config.llm)
    except RuntimeError:
        pass


def _mask_sensitive(data: dict) -> dict:
    """脱敏敏感字段（api_key / access_token），返回副本"""
    masked = dict(data)
    if "api_key" in masked and masked["api_key"]:
        masked["api_key"] = "***"
    if "llm" in masked and isinstance(masked["llm"], dict):
        llm = dict(masked["llm"])
        if llm.get("api_key"):
            llm["api_key"] = "***"
        masked["llm"] = llm
    if "onebot" in masked and isinstance(masked["onebot"], dict):
        ob = dict(masked["onebot"])
        if ob.get("access_token"):
            ob["access_token"] = "***"
        masked["onebot"] = ob
    return masked


router = APIRouter()


@router.get("", dependencies=[Depends(require_auth)])
async def get_config():
    """获取完整配置（敏感字段脱敏）"""
    return _mask_sensitive(_get_config_manager().to_dict())


@router.put("", dependencies=[Depends(require_auth)])
async def update_config(data: dict):
    """更新配置"""
    try:
        cfg = _get_config_manager()
        cfg.update(data)
        await _maybe_notify_bot()
        return {"message": "配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/bot")
async def get_bot_config():
    """获取 Bot 配置（无敏感字段）"""
    return _get_config_manager().bot.model_dump()


@router.put("/bot", dependencies=[Depends(require_auth)])
async def update_bot_config(data: dict):
    """更新 Bot 配置（深度合并，未传入字段保留原值）"""
    try:
        cfg = _get_config_manager()
        current = cfg.bot.model_dump()
        current.update(data)
        full = cfg.to_dict()
        full["bot"] = current
        cfg.update(full)
        await _maybe_notify_bot()
        return {"message": "Bot 配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/llm", dependencies=[Depends(require_auth)])
async def get_llm_config():
    """获取 LLM 配置（api_key 脱敏）"""
    data = _get_config_manager().llm.model_dump()
    if data.get("api_key"):
        data["api_key"] = "***"
    return data


@router.put("/llm", dependencies=[Depends(require_auth)])
async def update_llm_config(data: dict):
    """更新 LLM 配置（深度合并）"""
    try:
        cfg = _get_config_manager()
        current = cfg.llm.model_dump()
        # 如果前端传回脱敏的 "***"，保留原值
        if data.get("api_key") == "***":
            data["api_key"] = current["api_key"]
        current.update(data)
        full = cfg.to_dict()
        full["llm"] = current
        cfg.update(full)
        await _maybe_notify_bot()
        return {"message": "LLM 配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/onebot", dependencies=[Depends(require_auth)])
async def get_onebot_config():
    """获取 OneBot 连接配置（access_token 脱敏）"""
    data = _get_config_manager().onebot.model_dump()
    if data.get("access_token"):
        data["access_token"] = "***"
    return data
