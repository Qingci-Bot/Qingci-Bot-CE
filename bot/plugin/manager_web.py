# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""插件 Web 集成（PluginManager 的 Web 职责分组）

Mixin 形式并入 PluginManager：管理页面静态挂载、插件级 Web API 注册、
配置 Schema / 配置值读写（供 WebUI 自动渲染配置表单）。
状态（_web_app / _plugin_pages / _plugin_apis）由 PluginManager.__init__
统一初始化，本模块只承载方法；对外 API 仍是 manager 上的同名方法。
"""

import logging
import os
from collections.abc import Callable
from typing import Any, Union, cast, get_args, get_origin

from ..config_schema import is_secret_field as _is_secret_field
from .protocol.base import PluginBase

logger = logging.getLogger("qingci-bot.plugin.manager")


class _AuthStaticFiles:
    """带鉴权的插件静态文件服务（包装 StaticFiles）

    插件管理页面（/api/plugin-data/...）与 /api/plugin 鉴权对齐：
    - 配置了 api_key：要求 X-API-Key 头或 ?token= 查询参数
      （iframe/img 无法携带自定义请求头，故支持 query token 作为前端回退）
    - 未配置：仅环回来源 + Origin 为环回时放行
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from fastapi.staticfiles import StaticFiles

        self._inner = StaticFiles(*args, **kwargs)

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            from starlette.responses import Response

            await Response("Unauthorized", status_code=401)(scope, receive, send)
            return
        await self._inner(scope, receive, send)

    @staticmethod
    def _authorized(scope: dict) -> bool:
        import secrets
        from urllib.parse import parse_qs

        from api.auth import _get_configured_api_key, is_loopback_host, is_loopback_origin

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        query = parse_qs(scope.get("query_string", b"").decode(errors="replace"))
        key = headers.get(b"x-api-key", b"").decode() or (query.get("token") or [""])[0]
        configured = _get_configured_api_key()
        if configured is None:
            # 配置读取失败，fail-closed
            return False
        if configured:
            return bool(key) and secrets.compare_digest(key, configured)
        # 未配 key：环回来源 + Origin 环回（防任意网页驱动本地插件页面）
        client = scope.get("client") or ("", 0)
        host = client[0] if isinstance(client, (tuple, list)) else ""
        origin = headers.get(b"origin", b"").decode()
        return is_loopback_host(host) and (not origin or is_loopback_origin(origin))


def _schema_from_annotations(config_cls: type) -> dict:
    """从普通类的类型注解兜底生成 JSON Schema（非 pydantic Config 类）"""
    props: dict[str, dict] = {}
    annotations = getattr(config_cls, "__annotations__", {}) or {}
    for field_name, ann in annotations.items():
        if field_name.startswith("_"):
            continue
        props[field_name] = {
            "title": field_name,
            "type": _annotation_json_type(ann),
        }
    return {"type": "object", "properties": props, "required": []}


def _annotation_json_type(ann: Any) -> str:
    """将 Python 类型注解映射为 JSON Schema 类型（兜底用）"""
    if ann is bool:
        return "boolean"
    if ann is int:
        return "integer"
    if ann is float:
        return "number"
    if ann is str:
        return "string"
    if ann is list:
        return "array"
    if ann is dict:
        return "object"
    origin = get_origin(ann)
    if origin is Union:
        args = [a for a in get_args(ann) if a is not type(None)]
        if args:
            return _annotation_json_type(args[0])
    return "string"


class PluginWebMixin:
    """Web 管理页面 / 插件级 API / 配置 Schema（并入 PluginManager）"""

    # 状态由 PluginManager.__init__ 初始化（mixin 仅声明类型，不做赋值）
    _plugins: dict[str, PluginBase]
    _web_app: Any
    _plugin_pages: dict[str, list[dict]]
    _plugin_apis: dict[str, list[dict]]
    # 核心方法由 PluginManager 提供（update_config 写回后需重新校验应用配置）
    _load_plugin_config: Any
    # 注册表查询（PluginManager.get，供 webapi 动态解析插件实例）
    get: Callable[[str], PluginBase | None]

    # ---- Web 管理页面 ----

    def set_web_app(self, app) -> None:
        """注入 FastAPI 应用引用（由 create_app 后调用），并挂载已注册的插件页面与 API"""
        self._web_app = app
        self._mount_all_plugin_pages()
        self._mount_all_plugin_apis()

    def _mount_all_plugin_pages(self) -> None:
        """挂载所有已注册插件的静态文件目录"""
        if self._web_app is None:
            return

        for plugin_name, pages in self._plugin_pages.items():
            for idx, page in enumerate(pages):
                static_dir = page.get("static_dir", "")
                if static_dir and os.path.isdir(static_dir):
                    # 首个页面挂载在 /api/plugin-data/<name>/，其余页面按索引区分，
                    # 保证多页面插件每个入口都能打开对应静态目录
                    mount_path = (
                        f"/api/plugin-data/{plugin_name}"
                        if idx == 0
                        else f"/api/plugin-data/{plugin_name}/{idx}"
                    )
                    # 避免重复挂载
                    if not any(
                        r.path == mount_path for r in self._web_app.routes if hasattr(r, "path")
                    ):
                        try:
                            self._web_app.mount(
                                mount_path,
                                _AuthStaticFiles(directory=static_dir, html=True),
                                name=f"plugin-static-{plugin_name}-{idx}",
                            )
                            logger.info(
                                f"插件 {plugin_name} 管理页面已挂载: {mount_path} -> {static_dir}"
                            )
                        except Exception:
                            logger.exception(f"挂载插件 {plugin_name} 管理页面失败: {static_dir}")

    def get_plugin_pages(self, name: str) -> list[dict]:
        """获取指定插件的管理页面注册信息（不含 static_dir，附带访问 URL）"""
        pages = self._plugin_pages.get(name, [])
        return [
            {
                "title": p["title"],
                "icon": p["icon"],
                "url": (f"/api/plugin-data/{name}" if i == 0 else f"/api/plugin-data/{name}/{i}"),
            }
            for i, p in enumerate(pages)
        ]

    def _collect_plugin_pages(self, plugin: PluginBase) -> None:
        """从插件实例收集已注册的 Web 管理页面"""
        if plugin._pages:
            self._plugin_pages[plugin.name] = list(plugin._pages)
            if self._web_app is not None:
                self._mount_all_plugin_pages()

    def _remove_plugin_pages(self, name: str) -> None:
        """移除插件的 Web 管理页面注册，并摘除已挂载的静态路由"""
        self._plugin_pages.pop(name, None)
        if self._web_app is not None:
            # 摘除该插件名下所有静态挂载路由，避免卸载后路由残留
            self._web_app.routes[:] = [
                r
                for r in self._web_app.routes
                if not (
                    getattr(r, "name", "").startswith(f"plugin-static-{name}-")
                    or getattr(r, "path", "").startswith(f"/api/plugin-data/{name}")
                )
            ]

    # ---- 插件级 Web API ----

    def _mount_all_plugin_apis(self) -> None:
        """挂载所有已注册插件的 Web API 路由"""
        if self._web_app is None:
            return
        from .webapi import mount_plugin_apis

        for plugin_name, apis in self._plugin_apis.items():
            try:
                mount_plugin_apis(self._web_app, self, plugin_name, apis)
            except Exception:
                logger.exception(f"挂载插件 {plugin_name} Web API 失败")

    def _collect_plugin_apis(self, plugin: PluginBase) -> None:
        """从插件实例收集已注册的 Web API 并挂载"""
        apis = getattr(plugin, "_apis", None)
        if apis:
            self._plugin_apis[plugin.name] = list(apis)
            if self._web_app is not None:
                self._mount_all_plugin_apis()

    def _remove_plugin_apis(self, name: str) -> None:
        """移除插件的 Web API 注册（已挂载路由保留，请求时动态解析失效）"""
        self._plugin_apis.pop(name, None)

    # ---- 配置 schema 自动生成配置 UI ----

    def get_config_schema(self, name: str) -> dict | None:
        """获取插件的配置 JSON Schema（用于自动渲染 Web 配置表单）

        插件定义 Config 内嵌类（pydantic BaseModel）时，返回其 JSON Schema；
        定义为普通类时，从 __annotations__ 兜底生成简单 schema；
        未定义 Config 时返回 None。
        """
        plugin = self._plugins.get(name)
        if not plugin:
            return None
        config_cls = getattr(type(plugin), "Config", None)
        if config_cls is None or not isinstance(config_cls, type):
            return None
        # pydantic v2：直接导出 JSON Schema（含默认值、必填字段、描述）
        if hasattr(config_cls, "model_json_schema"):
            try:
                return cast(dict, config_cls.model_json_schema())
            except Exception:
                # 降级到注解兜底；留痕便于排查"配置表单字段缺失"的真实原因
                logger.debug(f"插件 {name} 的 JSON Schema 导出失败，回退注解推断", exc_info=True)
        return _schema_from_annotations(config_cls)

    def get_config_values(self, name: str) -> dict:
        """获取插件当前配置值（dict 形式）"""
        plugin = self._plugins.get(name)
        if not plugin or plugin.plugin_config is None:
            return {}
        cfg = plugin.plugin_config
        if hasattr(cfg, "model_dump"):
            try:
                values = cast(dict, cfg.model_dump())
            except Exception:
                values = dict(cfg)
        else:
            values = dict(cfg)
        # M19：敏感字段（token/secret/password/api_key 等）脱敏，WebUI 不回显明文
        return {k: ("" if (_is_secret_field(k) and v) else v) for k, v in values.items()}

    async def update_config(self, name: str, values: dict, bot) -> bool:
        """更新插件配置：写入 config.yaml 并重新校验应用到插件实例

        Args:
            name: 插件名
            values: 新配置值（dict）
            bot: Bot 实例

        Returns:
            是否成功
        """
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        try:
            # M19：与现有配置合并后写回——敏感字段提交空值时保留已有值，
            # 避免「读取脱敏→保存空串」误清空 token/密码等密钥。
            existing = plugin.plugin_config
            if existing is not None:
                existing_dict = (
                    existing.model_dump() if hasattr(existing, "model_dump") else dict(existing)
                )
            else:
                existing_dict = {}
            merged = dict(existing_dict)
            for key, value in (values or {}).items():
                if _is_secret_field(key) and value in ("", None):
                    continue
                merged[key] = value
            bot.config.set_plugin_config(name, merged)
            await self._load_plugin_config(plugin, bot)
            return True
        except Exception:
            logger.exception(f"更新插件 {name} 配置失败")
            return False
