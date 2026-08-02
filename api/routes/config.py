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


router = APIRouter()


@router.get("")
async def get_config():
    """获取完整配置"""
    return _get_config_manager().to_dict()


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
    """获取 Bot 配置"""
    return _get_config_manager().bot.model_dump()


@router.put("/bot", dependencies=[Depends(require_auth)])
async def update_bot_config(data: dict):
    """更新 Bot 配置"""
    cfg = _get_config_manager()
    full = cfg.to_dict()
    full["bot"] = data
    cfg.update(full)
    await _maybe_notify_bot()
    return {"message": "Bot 配置已更新"}


@router.get("/llm")
async def get_llm_config():
    """获取 LLM 配置"""
    return _get_config_manager().llm.model_dump()


@router.put("/llm", dependencies=[Depends(require_auth)])
async def update_llm_config(data: dict):
    """更新 LLM 配置"""
    cfg = _get_config_manager()
    full = cfg.to_dict()
    full["llm"] = data
    cfg.update(full)
    await _maybe_notify_bot()
    return {"message": "LLM 配置已更新"}


@router.get("/onebot")
async def get_onebot_config():
    """获取 OneBot 连接配置"""
    return _get_config_manager().onebot.model_dump()


@router.post("/llm/test", dependencies=[Depends(require_auth)])
async def test_llm(data: dict):
    """测试 LLM 连接（使用提交的配置）"""
    from bot.llm import LLMManager
    from bot.config import LLMConfig
    try:
        llm_cfg = LLMConfig(**data)
        llm = LLMManager(llm_cfg)
        ok = await llm.check_availability()
        await llm.close()
        return {"available": ok}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))