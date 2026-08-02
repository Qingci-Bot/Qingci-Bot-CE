"""插件管理接口"""

from fastapi import APIRouter, HTTPException, Depends

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth


router = APIRouter()


def _get_bot_instance():
    try:
        return _get_bot()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Bot 未初始化，请先启动 Bot 服务")


@router.get("")
async def list_plugins():
    """获取插件列表"""
    bot = _get_bot_instance()
    plugins = []
    for name, plugin in bot.plugin_manager.plugins.items():
        plugins.append({
            "name": plugin.name,
            "version": plugin.version,
            "author": plugin.author,
            "description": plugin.description,
        })
    return plugins


@router.get("/{name}")
async def get_plugin(name: str):
    """获取插件详情"""
    bot = _get_bot_instance()
    plugin = bot.plugin_manager.get(name)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    return {
        "name": plugin.name,
        "version": plugin.version,
        "author": plugin.author,
        "description": plugin.description,
    }


@router.post("/{name}/reload", dependencies=[Depends(require_auth)])
async def reload_plugin(name: str):
    """重载插件"""
    bot = _get_bot_instance()
    await bot.plugin_manager.reload(name, bot)
    return {"message": f"插件 {name} 已重载"}


@router.post("/load", dependencies=[Depends(require_auth)])
async def load_plugin(data: dict):
    """加载外部插件"""
    module_path = data.get("module_path")
    if not module_path:
        raise HTTPException(status_code=400, detail="缺少 module_path")
    bot = _get_bot_instance()
    ok = await bot.plugin_manager.load_external(module_path, bot)
    if not ok:
        raise HTTPException(status_code=400, detail="加载失败")
    return {"message": "插件已加载"}


@router.delete("/{name}", dependencies=[Depends(require_auth)])
async def unload_plugin(name: str):
    """卸载插件"""
    bot = _get_bot_instance()
    await bot.plugin_manager.unload(name)
    return {"message": f"插件 {name} 已卸载"}