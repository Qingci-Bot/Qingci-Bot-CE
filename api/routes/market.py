"""插件市场接口"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.audit import record_audit
from api.auth import require_auth
from bot.core.bot import get_bot as _get_bot
from bot.plugin.market import DEFAULT_MARKET_URL, MarketError, MarketManager

logger = logging.getLogger("qingci-bot.api.market")

router = APIRouter()


class MarketActionRequest(BaseModel):
    """市场安装/更新请求体"""

    name: str = Field(..., description="插件名（与市场索引 name 一致）")


def _get_bot_instance():
    try:
        return _get_bot()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Bot 未初始化，请先启动 Bot 服务") from None


def _get_market_manager(bot) -> MarketManager:
    """获取（或惰性创建）市场管理器，配置优先于默认值"""
    manager = getattr(bot, "_market_manager", None)
    if manager is None:
        cfg = getattr(bot.config, "market", None)
        url = str(getattr(cfg, "url", "") or "").strip() or DEFAULT_MARKET_URL
        refresh = float(getattr(cfg, "refresh_interval", 3600) or 3600)
        manager = MarketManager(url=url, refresh_interval=refresh)
        bot._market_manager = manager  # type: ignore[attr-defined]
    return manager


@router.get("", dependencies=[Depends(require_auth)])
async def list_market():
    """获取插件市场列表（含已安装/可更新状态）"""
    bot = _get_bot_instance()
    try:
        items = await _get_market_manager(bot).list_market(bot)
    except MarketError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception:
        logger.exception("获取插件市场列表失败")
        raise HTTPException(status_code=500, detail="获取插件市场列表失败") from None
    return items


@router.get("/info", dependencies=[Depends(require_auth)])
async def market_info():
    """获取插件市场元信息（名称/插件数/索引更新时间）"""
    bot = _get_bot_instance()
    try:
        return await _get_market_manager(bot).market_info()
    except MarketError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception:
        logger.exception("获取插件市场元信息失败")
        raise HTTPException(status_code=500, detail="获取插件市场元信息失败") from None


@router.post("/install", dependencies=[Depends(require_auth)])
async def install_plugin(data: MarketActionRequest, request: Request):
    """安装市场插件（git 克隆/HTTP 归档 + 依赖隔离 + 加载）"""
    bot = _get_bot_instance()
    try:
        ok = await _get_market_manager(bot).install(bot, data.name)
        if not ok:
            raise HTTPException(status_code=500, detail=f"插件 {data.name} 安装失败")
        await record_audit("market_install", f"从市场安装插件: {data.name}", request)
        return {"message": f"插件 {data.name} 安装成功"}
    except MarketError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"安装市场插件失败: {data.name}")
        raise HTTPException(status_code=500, detail="安装失败，详见服务端日志") from None


@router.post("/update", dependencies=[Depends(require_auth)])
async def update_plugin(data: MarketActionRequest, request: Request):
    """更新市场插件（覆盖重装 + 重载）"""
    bot = _get_bot_instance()
    try:
        ok = await _get_market_manager(bot).update(bot, data.name)
        if not ok:
            raise HTTPException(status_code=500, detail=f"插件 {data.name} 更新失败")
        await record_audit("market_update", f"更新市场插件: {data.name}", request)
        return {"message": f"插件 {data.name} 更新成功"}
    except MarketError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception:
        logger.exception(f"更新市场插件失败: {data.name}")
        raise HTTPException(status_code=500, detail="更新失败，详见服务端日志") from None


@router.post("/refresh", dependencies=[Depends(require_auth)])
async def refresh_market():
    """强制刷新市场索引缓存"""
    bot = _get_bot_instance()
    try:
        index = await _get_market_manager(bot).refresh()
        return {"message": f"市场索引已刷新（{index.name}，{len(index.plugins)} 个插件）"}
    except MarketError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception:
        logger.exception("刷新市场索引失败")
        raise HTTPException(status_code=500, detail="刷新市场索引失败") from None
