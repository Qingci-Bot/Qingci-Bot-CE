"""配置管理接口"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import ValidationError

from bot.core.bot import get_bot as _get_bot
from bot.config import ConfigManager
from api.auth import require_auth

logger = logging.getLogger("qingci-bot.api.config")

_config_lock = asyncio.Lock()


def _get_config_manager() -> ConfigManager:
    """获取 Bot 实例的 ConfigManager，若 Bot 未初始化则使用自定义路径"""
    try:
        bot = _get_bot()
        return bot.config
    except RuntimeError:
        from api.auth import _config_path
        from bot.config import DEFAULT_CONFIG_PATH
        cfg = ConfigManager(_config_path or DEFAULT_CONFIG_PATH)
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
    async with _config_lock:
        try:
            cfg = _get_config_manager()
            cfg.update(data)
            await _maybe_notify_bot()
            return {"message": "配置已更新"}
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("配置更新失败")
            raise HTTPException(status_code=500, detail=f"内部错误: {e}")


@router.get("/bot", dependencies=[Depends(require_auth)])
async def get_bot_config():
    """获取 Bot 配置（无敏感字段）"""
    return _get_config_manager().bot.model_dump()


@router.put("/bot", dependencies=[Depends(require_auth)])
async def update_bot_config(data: dict):
    """更新 Bot 配置（深度合并，未传入字段保留原值）"""
    async with _config_lock:
        try:
            cfg = _get_config_manager()
            current = cfg.bot.model_dump()
            for k, v in data.items():
                if v is not None:
                    current[k] = v
            full = cfg.to_dict()
            full["bot"] = current
            cfg.update(full)
            await _maybe_notify_bot()
            return {"message": "Bot 配置已更新"}
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("配置更新失败")
            raise HTTPException(status_code=500, detail=f"内部错误: {e}")


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
    async with _config_lock:
        try:
            cfg = _get_config_manager()
            current = cfg.llm.model_dump()
            # 过滤 None 值与脱敏占位 "***"，避免覆盖已有配置
            for k, v in data.items():
                if v is not None and v != "***":
                    current[k] = v
            full = cfg.to_dict()
            full["llm"] = current
            cfg.update(full)
            await _maybe_notify_bot()
            return {"message": "LLM 配置已更新"}
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("配置更新失败")
            raise HTTPException(status_code=500, detail=f"内部错误: {e}")


@router.get("/onebot", dependencies=[Depends(require_auth)])
async def get_onebot_config():
    """获取 OneBot 连接配置（access_token 脱敏）"""
    data = _get_config_manager().onebot.model_dump()
    if data.get("access_token"):
        data["access_token"] = "***"
    return data
