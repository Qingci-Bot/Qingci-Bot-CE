"""插件级 Web API 适配器

将插件在 on_load 中经 `PluginBase.register_api` 注册的 HTTP 接口挂载到
FastAPI 应用（统一前缀 `/api/plugin-web/<plugin_name>/<path>`），鉴权对齐
现有 API 体系（X-API-Key）。

设计要点：
- 路由在插件加载后动态挂载（`app.add_api_route`），无需重启服务；
- 端点按**请求时**动态解析插件实例与 handler（而非闭包捕获），插件热重载/
  卸载后旧路由自动指向新实现或返回 404，避免残留路由失效或串实例；
- handler 异常统一转为 500 JSON 响应，不污染全局异常处理语义；
- 同一路径重复挂载自动跳过（幂等）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response

from api.auth import require_auth

if TYPE_CHECKING:
    from .manager import PluginManager

logger = logging.getLogger("qingci-bot.plugin.webapi")

# 插件 Web API 统一挂载前缀
API_BASE = "/api/plugin-web"


def _normalize_result(result: Any) -> Response:
    """把 handler 返回值归一化为 FastAPI Response

    支持：Response（含 FileResponse/JSONResponse）原样返回；
    `(data, status_code)` 二元组按 JSON 序列化并指定状态码；
    dict / list / str 等自动 JSON 序列化。
    """
    if isinstance(result, Response):
        return result
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], int):
        data, status_code = result
        if isinstance(data, Response):
            return data
        return JSONResponse(data, status_code=status_code)
    return JSONResponse(result)


def _find_api(plugin: Any, path: str, method: str) -> dict[str, Any] | None:
    """按路径 + 方法定位插件**当前**注册的 API（热重载后指向新实现）"""
    apis = getattr(plugin, "_apis", None)
    if not isinstance(apis, list):
        return None
    norm = path.strip("/")
    for raw in apis:
        if not isinstance(raw, dict):
            continue
        api = cast(dict[str, Any], raw)
        if str(api.get("path") or "").strip("/") == norm and method in (
            api.get("methods") or ["GET"]
        ):
            return api
    return None


def _build_endpoint(
    manager: PluginManager, plugin_name: str, api_path: str
) -> Callable[..., Awaitable[Response]]:
    """构造路由端点：请求时动态解析插件实例与 handler"""

    async def _endpoint(request: Request) -> Response:
        plugin = manager.get(plugin_name)
        if plugin is None:
            return JSONResponse({"detail": "插件未加载"}, status_code=404)
        api = _find_api(plugin, api_path, request.method)
        if api is None:
            return JSONResponse({"detail": "插件 API 不存在"}, status_code=404)
        handler: Callable[..., Any] = api["handler"]
        try:
            result = handler(request)
            if isinstance(result, Awaitable):
                result = await result
        except Exception as exc:
            logger.exception(
                f"插件 {plugin_name} Web API 异常: {request.method} {request.url.path}"
            )
            return JSONResponse(
                {"detail": "插件 Web API 内部错误", "error_type": type(exc).__name__},
                status_code=500,
            )
        return _normalize_result(result)

    return _endpoint


def mount_plugin_apis(
    app: Any,
    manager: PluginManager,
    plugin_name: str,
    apis: list[dict[str, Any]],
) -> None:
    """把插件的 API 注册挂载到 FastAPI 应用（同路径已存在时跳过）"""
    for api in apis:
        path = str(api.get("path") or "").strip("/")
        full = f"{API_BASE}/{plugin_name}" + (f"/{path}" if path else "")
        if any(getattr(r, "path", None) == full for r in app.routes):
            continue
        methods = [str(m).upper() for m in (api.get("methods") or ["GET"])]
        endpoint = _build_endpoint(manager, plugin_name, path)
        app.add_api_route(
            full,
            endpoint,
            methods=methods,
            dependencies=[Depends(require_auth)],
            name=f"plugin-api-{plugin_name}-{path or 'root'}",
            tags=["PluginWeb"],
        )
        logger.info(f"插件 {plugin_name} Web API 已挂载: {full} ({', '.join(methods)})")
