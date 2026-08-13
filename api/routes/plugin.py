"""插件管理接口"""

import logging
import re

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth
from api.audit import record_audit

logger = logging.getLogger("qingci-bot.api.plugin")


class LoadPluginRequest(BaseModel):
    """加载外部插件请求体（类型非法时由 FastAPI 返回 422）"""
    module_path: str = Field(..., description="插件模块路径，如 plugins.my_plugin")


router = APIRouter()

# 模块路径白名单：仅允许 plugins.* / bot.plugin.builtin.* 前缀
# 防止加载 os / subprocess 等危险标准库模块
_ALLOWED_MODULE_PREFIXES = ("plugins.", "bot.plugin.builtin.")

# 内置插件白名单：不允许卸载（与 bot/plugin/builtin/ 下各插件的 name 属性一致）
_BUILTIN_PLUGINS = {"chat", "admin", "help", "imagegen", "knowledge"}


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
    """获取插件列表（含状态、分类、Web 管理页面）"""
    bot = _get_bot_instance()
    plugins = []
    for name, plugin in bot.plugin_manager.plugins.items():
        plugins.append({
            "name": plugin.name,
            "version": plugin.version,
            "author": plugin.author,
            "description": plugin.description,
            "category": plugin.category,
            "status": plugin.status.value,
            "enabled": plugin.enabled,
            "pages": bot.plugin_manager.get_plugin_pages(plugin.name),
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
        "category": plugin.category,
        "status": plugin.status.value,
        "enabled": plugin.enabled,
        "require": plugin.require,
    }


@router.post("/{name}/reload", dependencies=[Depends(require_auth)])
async def reload_plugin(name: str, request: Request):
    """重载插件"""
    bot = _get_bot_instance()
    if not bot.plugin_manager.get(name):
        raise HTTPException(status_code=404, detail=f"插件 {name} 不存在")
    try:
        await bot.plugin_manager.reload(name, bot)
        await record_audit("plugin_reload", f"重载插件: {name}", request)
        return {"message": f"插件 {name} 已重载"}
    except Exception:
        logger.exception(f"插件 {name} 重载失败")
        raise HTTPException(status_code=500, detail="插件操作失败，详见服务端日志")


@router.post("/load", dependencies=[Depends(require_auth)])
async def load_plugin(data: LoadPluginRequest, request: Request):
    """加载外部插件（仅允许 plugins.* 前缀的模块路径）"""
    module_path = data.module_path
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
    await record_audit("plugin_load", f"加载外部插件: {module_path}", request)
    return {"message": "插件已加载"}


@router.delete("/{name}", dependencies=[Depends(require_auth)])
async def unload_plugin(name: str, request: Request):
    """卸载插件"""
    bot = _get_bot_instance()
    if name in _BUILTIN_PLUGINS:
        raise HTTPException(status_code=400, detail=f"不允许卸载内置插件 {name}")
    if not bot.plugin_manager.get(name):
        raise HTTPException(status_code=404, detail=f"插件 {name} 不存在")
    await bot.plugin_manager.unload(name)
    await record_audit("plugin_unload", f"卸载插件: {name}", request)
    return {"message": f"插件 {name} 已卸载"}


@router.post("/{name}/disable", dependencies=[Depends(require_auth)])
async def disable_plugin(name: str, request: Request):
    """禁用插件（保留实例，跳过事件分发）"""
    bot = _get_bot_instance()
    if not bot.plugin_manager.get(name):
        raise HTTPException(status_code=404, detail=f"插件 {name} 不存在")
    await bot.plugin_manager.disable(name)
    await record_audit("plugin_disable", f"禁用插件: {name}", request)
    return {"message": f"插件 {name} 已禁用"}


@router.post("/{name}/enable", dependencies=[Depends(require_auth)])
async def enable_plugin(name: str, request: Request):
    """启用插件（恢复事件分发）"""
    bot = _get_bot_instance()
    if not bot.plugin_manager.get(name):
        raise HTTPException(status_code=404, detail=f"插件 {name} 不存在")
    await bot.plugin_manager.enable(name)
    await record_audit("plugin_enable", f"启用插件: {name}", request)
    return {"message": f"插件 {name} 已启用"}


@router.get("/{name}/metrics", dependencies=[Depends(require_auth)])
async def get_plugin_metrics(name: str):
    """获取插件执行指标"""
    bot = _get_bot_instance()
    if not bot.plugin_manager.get(name):
        raise HTTPException(status_code=404, detail=f"插件 {name} 不存在")
    return bot.plugin_manager.get_metrics(name)


@router.get("/discover/metadata", dependencies=[Depends(require_auth)])
async def discover_plugins_metadata():
    """无导入发现：扫描 plugins/ 目录中的 plugin.json 元数据"""
    from bot.paths import app_root
    bot = _get_bot_instance()
    directory = app_root() / "plugins"
    return bot.plugin_manager.discover_metadata(directory)
