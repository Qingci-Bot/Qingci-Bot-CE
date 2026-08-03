"""插件管理接口"""

import re

from fastapi import APIRouter, HTTPException, Depends

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth


router = APIRouter()

# 模块路径白名单：仅允许 plugins.* / bot.plugin.builtin.* 前缀
# 防止加载 os / subprocess 等危险标准库模块
_ALLOWED_MODULE_PREFIXES = ("plugins.", "bot.plugin.builtin.")

# 内置插件白名单：不允许卸载
_BUILTIN_PLUGINS = {"chat", "admin"}


def _is_safe_module_path(module_path: str) -> bool:
    """检查模块路径是否安全（仅允许白名单前缀，禁止相对导入和标准库）"""
    if not module_path or module_path.startswith("."):
        return False
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)*$", module_path):
        return False
    return any(
        module_path.startswith(prefix)
        for prefix in _ALLOWED_MODULE_PREFIXES
    )


def _get_bot_instance():
    try:
        return _get_bot()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Bot 未初始化，请先启动 Bot 服务")


@router.get("", dependencies=[Depends(require_auth)])
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


@router.get("/{name}", dependencies=[Depends(require_auth)])
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
    if not bot.plugin_manager.get(name):
        raise HTTPException(status_code=404, detail=f"插件 {name} 不存在")
    try:
        await bot.plugin_manager.reload(name, bot)
        return {"message": f"插件 {name} 已重载"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/load", dependencies=[Depends(require_auth)])
async def load_plugin(data: dict):
    """加载外部插件（仅允许 plugins.* 前缀的模块路径）"""
    module_path = data.get("module_path")
    if not module_path:
        raise HTTPException(status_code=400, detail="缺少 module_path")
    if not _is_safe_module_path(module_path):
        raise HTTPException(
            status_code=400,
            detail=f"不安全的模块路径，仅允许 {', '.join(_ALLOWED_MODULE_PREFIXES)} 前缀",
        )
    bot = _get_bot_instance()
    ok = await bot.plugin_manager.load_external(module_path, bot)
    if not ok:
        raise HTTPException(status_code=400, detail="加载失败")
    return {"message": "插件已加载"}


@router.delete("/{name}", dependencies=[Depends(require_auth)])
async def unload_plugin(name: str):
    """卸载插件"""
    bot = _get_bot_instance()
    if name in _BUILTIN_PLUGINS:
        raise HTTPException(status_code=400, detail=f"不允许卸载内置插件 {name}")
    if not bot.plugin_manager.get(name):
        raise HTTPException(status_code=404, detail=f"插件 {name} 不存在")
    await bot.plugin_manager.unload(name)
    return {"message": f"插件 {name} 已卸载"}
