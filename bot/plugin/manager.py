"""插件管理器"""

import asyncio
import importlib
import json
import logging
import os
import pkgutil
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from ._proc import NO_WINDOW_FLAG
from .deps import ensure_dependencies, ensure_in_sys_path
from .protocol.base import PluginBase, PluginStatus
from .protocol.matcher import (
    Matcher,
    begin_module_collection,
    end_module_collection,
)

logger = logging.getLogger("qingci-bot.plugin.manager")

# 内置插件显式清单（PyInstaller 打包后 pkgutil 无法扫描 PYZ 归档内模块，
# 需回退到显式清单加载；新增内置插件时同步更新此处与 qingci-bot-ce.spec 的 hiddenimports）
_BUILTIN_PLUGINS: tuple[str, ...] = ("admin", "chat", "help", "imagegen", "knowledge")

# 插件名合法性：仅允许字母/数字/下划线/连字符，禁止路径穿越（../ 等）
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")

# 归档下载/解压大小上限（防 zip 炸弹撑爆磁盘）
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024  # 下载上限 200MB
_MAX_EXTRACT_BYTES = 1024 * 1024 * 1024  # 解压总量上限 1GB


def _expand_subcommand_matchers(matchers: list[Matcher], owner: str) -> None:
    """展开 on_command 子指令 matcher：parent.meta["sub_matchers"] → matchers

    SDK 在 on_command 创建 parent 时把全部子指令 matcher 挂到
    `parent.meta["sub_matchers"]`，覆盖两类注册场景：
    - 模块级（import 阶段）：子 matcher 已被收集器收集进 matchers → 按对象 id
      去重，避免双份；
    - on_load 运行时注册：子 matcher 仅挂在 parent.meta → 随 parent 展开，
      修复"父指令已排除子指令、子指令 matcher 又不注册"导致的子命令不触发。
    """
    known_ids = {id(m) for m in matchers}
    for m in list(matchers):
        if "command" in m.meta and "sub_matchers" not in m.meta:
            # SDK < 1.13.1 不挂 sub_matchers：on_load 运行时注册的子指令会静默丢失
            logger.warning(
                f"插件 {owner} 的命令 matcher {m.meta.get('command')!r} 缺少 "
                f"sub_matchers 元数据（qingci-plugin-sdk 版本过旧，需 >=1.13.1），"
                f"on_load 注册的子指令可能不触发"
            )
        for sub in m.meta.get("sub_matchers") or []:
            if id(sub) in known_ids:
                continue
            if not sub.owner:
                sub.owner = owner
            matchers.append(sub)
            known_ids.add(id(sub))


class _ReloadRWLock:
    """热重载读写屏障：事件分发共享读、重载独占写

    防止重载窗口内（importlib.reload 就地替换模块 dict / 注册表替换）
    事件分发读到半新半旧的插件状态。同任务重入安全：
    - handler 内触发 reload（读→写）：acquire_write 返回 False，跳过写锁
    - 写锁持有者内重新读（写→读）：acquire_read 直接放行
    - 嵌套写（reload 内部再走 _register_from_module）：幂等放行
    """

    def __init__(self) -> None:
        self._readers: set[asyncio.Task] = set()
        self._writer: asyncio.Task | None = None
        self._cond = asyncio.Condition()

    def _current_task(self) -> asyncio.Task | None:
        """当前任务；事件循环外调用时返回 None（防御性，不抛错）"""
        return asyncio.current_task()

    async def acquire_read(self) -> None:
        task = self._current_task()
        if task is None:
            return  # 事件循环外调用：无锁语义
        if task is self._writer:
            return  # 写锁持有者内重入读：直接放行
        async with self._cond:
            while self._writer is not None:
                await self._cond.wait()
            self._readers.add(task)

    async def release_read(self) -> None:
        task = self._current_task()
        if task is None:
            return
        if task is self._writer:
            return
        async with self._cond:
            if task in self._readers:
                self._readers.discard(task)
                # 无论是否有写者在等都要 notify：等待中的写者尚未设置
                # _writer，其唤醒依赖读者释放时通知（while 循环防误唤醒）
                if not self._readers:
                    self._cond.notify_all()

    async def acquire_write(self) -> bool:
        """返回 False 表示本任务已持读锁（读→写重入），调用方须跳过写锁"""
        task = self._current_task()
        if task is None:
            return False
        if task in self._readers:
            return False
        async with self._cond:
            while self._writer is not None or self._readers:
                if self._writer is task:
                    return True  # 嵌套写：已由本任务持有，幂等放行
                await self._cond.wait()
            self._writer = task
        return True

    async def release_write(self) -> None:
        task = self._current_task()
        if task is None or self._writer is not task:
            return
        async with self._cond:
            self._writer = None
            self._cond.notify_all()


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


def _is_valid_plugin_name(name: str) -> bool:
    """校验插件名是否合法（用于市场安装等从外部输入获取插件名的场景）"""
    return bool(_PLUGIN_NAME_RE.fullmatch(name))


def _sha256_file(path: Path) -> str:
    """计算文件 SHA256（小文件分块读取，防止归档大文件一次读入内存）"""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sniff_archive_format(path: Path) -> str | None:
    """按文件头嗅探归档格式：zip / tar / 其他（扩展名缺失时的兜底）"""
    import tarfile
    import zipfile

    try:
        if zipfile.is_zipfile(path):
            return "zip"
        if tarfile.is_tarfile(path):
            return "tar"
    except OSError:
        return None
    return None


@dataclass
class MatcherMetrics:
    """单个 Matcher 的执行指标（含阶段耗时细分）"""

    call_count: int = 0
    total_time_ms: float = 0.0
    error_count: int = 0
    last_call_time: float = 0.0
    # 阶段耗时累计（毫秒）：permission 检查 / rule 检查 / handler 执行
    permission_time_ms: float = 0.0
    rule_time_ms: float = 0.0
    handler_time_ms: float = 0.0

    @property
    def avg_time_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_time_ms / self.call_count

    @property
    def avg_handler_time_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.handler_time_ms / self.call_count


def _parse_version_spec(dep_spec: str) -> tuple[str, SpecifierSet | None]:
    """解析依赖声明，返回 (name, version_spec)。

    示例：
        "chat"           -> ("chat", None)
        "chat>=1.0"      -> ("chat", SpecifierSet(">=1.0"))
        "chat>=1.0,<2.0" -> ("chat", SpecifierSet(">=1.0,<2.0"))
    """
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9_]*)\s*(.*)$", dep_spec)
    if not match:
        raise ValueError(f"无效的依赖声明: {dep_spec}")
    name = match.group(1)
    spec_str = match.group(2).strip()
    if spec_str:
        try:
            return name, SpecifierSet(spec_str)
        except Exception:
            raise ValueError(f"无效的版本约束: {dep_spec}") from None
    return name, None


def _load_plugin_json(directory: Path) -> dict | None:
    """从目录中加载 plugin.json 元数据（若存在）"""
    json_path = directory / "plugin.json"
    if not json_path.is_file():
        return None
    try:
        return cast(dict, json.loads(json_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        logger.warning(f"plugin.json 解析失败: {json_path}")
        return None


def _sdk_plugin_base():
    """惰性获取独立插件 SDK 的 PluginBase 基类（SDK 未安装时返回 None）

    外部插件可能基于 bot.plugin.base.PluginBase（内置式）或
    qingci_plugin_sdk.base.PluginBase（独立 SDK 式）开发，两者是镜像类、
    无继承关系。管理器需同时识别这两类插件。
    """
    try:
        from qingci_plugin_sdk.base import PluginBase as _SdkBase

        return _SdkBase
    except ImportError:
        return None


def _module_plugin_classes(module, sdk_base) -> list[type]:
    """返回模块中定义的插件类（bot 基类或 SDK 基类的直接子类）"""
    result: list[type] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if not isinstance(attr, type) or attr.__module__ != module.__name__:
            continue
        if attr is PluginBase or (sdk_base is not None and attr is sdk_base):
            continue
        if issubclass(attr, PluginBase):
            result.append(attr)
        elif sdk_base is not None and issubclass(attr, sdk_base):
            result.append(attr)
    return result


class PluginManager:
    """插件管理器：加载、卸载、热重载、状态管理、指标监控

    支持两种 Matcher 注册方式：
    1. 模块级装饰器 @on_command(...)：加载模块时自动收集
    2. 插件内 self.matchers.append(...)：在 on_load 中手动注册
    """

    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}
        self._cached_matchers: list[Matcher] | None = None
        # 热重载互斥锁：importlib.reload + 注册非原子，串行化避免并发
        # reload/install 竞态（新旧插件实例并存、matcher 双份）
        self._reload_lock = asyncio.Lock()
        # 重载读写屏障：事件分发共享读、重载独占写，防止重载窗口内
        # 分发读到半新半旧的模块 dict / 注册表
        self._reload_rw = _ReloadRWLock()
        # 调度缓存签名：记录构建缓存时各插件 matcher 数量，检测运行期动态增删
        self._matchers_sig: tuple | None = None
        # 事件类型倒排索引: event_type -> 该类型的 Matcher（保持优先级升序）
        self._matcher_index: dict[str, list[Matcher]] = {}
        # Matcher 执行指标: owner_name -> Matcher 实例 -> MatcherMetrics
        self._metrics: dict[str, dict[Matcher, MatcherMetrics]] = {}
        # plugin.json 预取缓存: module_path -> metadata dict
        self._metadata_cache: dict[str, dict] = {}
        # Web 管理页面注册信息: plugin_name -> [{"title": ..., "icon": ..., "static_dir": ...}]
        self._plugin_pages: dict[str, list[dict]] = {}
        # 插件级 Web API 注册信息: plugin_name -> [{"path": ..., "handler": ..., ...}]
        self._plugin_apis: dict[str, list[dict]] = {}
        # FastAPI 应用引用（由 create_app 后注入）
        self._web_app = None
        # 全局 i18n 语言（插件翻译默认语言，由 config.lang 设置）
        self._i18n_locale = "zh-CN"
        # 插件注册的 LLM 工具名: plugin_name -> [full_tool_name]，卸载时注销
        self._plugin_tools: dict[str, list[str]] = {}
        # 插件加载失败记录: plugin_name -> 错误摘要（供 WebUI 展示，替代静默失败）
        self._load_errors: dict[str, str] = {}

    def set_i18n_locale(self, locale: str) -> None:
        """设置全局语言并刷新已加载插件的翻译"""
        self._i18n_locale = locale or "zh-CN"
        for plugin in self._plugins.values():
            self._load_i18n(plugin)

    def _load_i18n(self, plugin: PluginBase) -> None:
        """为插件加载翻译资源（i18n/<locale>.json）"""
        from ..i18n import load_plugin_i18n

        plugin.i18n.locale = self._i18n_locale
        plugin.i18n._data.clear()
        loaded = load_plugin_i18n(plugin)
        if loaded.locale == self._i18n_locale and loaded._data:
            plugin.i18n._data.update(loaded._data)

    @property
    def plugins(self) -> dict[str, PluginBase]:
        return self._plugins

    def get(self, name: str) -> PluginBase | None:
        return self._plugins.get(name)

    def all_matchers(self, post_type: str | None = None) -> list[Matcher]:
        """收集所有已启用插件的 Matcher（用于调度），结果已按优先级升序排序

        约定：priority 越小越先执行。返回缓存副本，防止调用方污染缓存。
        仅 LOADED 状态的插件参与调度，disabled 的 Matcher 被跳过。

        post_type 非空时通过事件类型倒排索引直接返回该事件类型的 Matcher，
        避免每次分发都对全部 Matcher 做线性扫描过滤（注意/请求等低频
        事件类型收益尤其明显）。
        """
        if self._cached_matchers is None or self._matchers_sig != self._matchers_signature():
            self._matchers_sig = self._matchers_signature()
            result = []
            for plugin in self._plugins.values():
                if plugin.matchers and plugin.status == PluginStatus.LOADED:
                    for m in plugin.matchers:
                        if not m.disabled:
                            result.append(m)
            result.sort(key=lambda m: m.priority)
            self._cached_matchers = result
            # 构建事件类型倒排索引（各类型子列表随全量排序保持优先级升序）
            index: dict[str, list[Matcher]] = {}
            for m in result:
                index.setdefault(m.event_type, []).append(m)
            self._matcher_index = index
        if post_type:
            return list(self._matcher_index.get(post_type, []))
        return list(self._cached_matchers)

    def _matchers_signature(self) -> tuple:
        """当前已加载插件的 matcher 数量签名，用于检测运行期动态增删 matcher

        plugin.matchers 是 SDK 公开可变列表，插件可能在 on_load 之后运行时
        append/remove（一次性 matcher 等场景）；通过签名比对使调度缓存失效，
        避免新增 matcher 静默不参与调度。
        """
        return tuple(
            (name, len(p.matchers) if p.matchers else 0)
            for name, p in self._plugins.items()
            if p.status == PluginStatus.LOADED
        )

    def _invalidate_matchers_cache(self) -> None:
        self._cached_matchers = None
        self._matcher_index = {}

    # ---- 指标 ----

    def record_metric(
        self,
        matcher: Matcher,
        elapsed_ms: float,
        is_error: bool = False,
        handler_ms: float | None = None,
        permission_ms: float | None = None,
        rule_ms: float | None = None,
    ) -> None:
        """记录一次 Matcher 执行指标（elapsed_ms 为总耗时，阶段耗时可选细分）"""
        owner = getattr(matcher, "owner", "__unknown__")
        if owner not in self._metrics:
            self._metrics[owner] = {}
        if matcher not in self._metrics[owner]:
            self._metrics[owner][matcher] = MatcherMetrics()
        m = self._metrics[owner][matcher]
        m.call_count += 1
        m.total_time_ms += elapsed_ms
        if permission_ms is not None:
            m.permission_time_ms += permission_ms
        if rule_ms is not None:
            m.rule_time_ms += rule_ms
        if handler_ms is not None:
            m.handler_time_ms += handler_ms
        if is_error:
            m.error_count += 1
        m.last_call_time = time.time()

    def get_metrics(self, plugin_name: str) -> list[dict]:
        """获取指定插件的执行指标"""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return []
        result = []
        for matcher in plugin.matchers or []:
            owner_metrics = self._metrics.get(plugin_name, {})
            m = owner_metrics.get(matcher)
            if m is None:
                continue
            handler_name = getattr(matcher.handler, "__name__", "unknown")
            result.append(
                {
                    "handler": handler_name,
                    "event_type": matcher.event_type,
                    "priority": matcher.priority,
                    "description": (matcher.meta or {}).get("description", ""),
                    "call_count": m.call_count,
                    "avg_time_ms": round(m.avg_time_ms, 2),
                    "total_time_ms": round(m.total_time_ms, 2),
                    "error_count": m.error_count,
                    "last_call_time": m.last_call_time,
                    # 阶段耗时均值（毫秒）：定位到 rule / permission / handler 环节
                    "avg_permission_ms": round(
                        m.permission_time_ms / m.call_count if m.call_count else 0.0, 2
                    ),
                    "avg_rule_ms": round(m.rule_time_ms / m.call_count if m.call_count else 0.0, 2),
                    "avg_handler_ms": round(m.avg_handler_time_ms, 2),
                }
            )
        return result

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
                pass
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

    # ---- 元数据发现 ----

    def discover_metadata(self, directory: Path) -> list[dict]:
        """扫描目录中的 plugin.json 元数据，无需导入模块

        目录型插件（plugins/<name>/plugin.json）为主形态；同时兼容
        plugins/ 根目录下直接放置的 plugin.json（文件型插件共用）。
        """
        results: list[dict] = []
        if not directory.is_dir():
            return results
        # 根目录直接放置的 plugin.json
        root_meta = _load_plugin_json(directory)
        if root_meta:
            self._metadata_cache["."] = root_meta
            results.append(root_meta)
        # 目录型插件：plugins/<name>/plugin.json
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            meta = _load_plugin_json(child)
            if meta:
                self._metadata_cache[child.name] = meta
                results.append(meta)
        return results

    # ---- 加载 ----

    async def load_builtin(self, bot) -> None:
        """加载内置插件

        优先通过 pkgutil 扫描 builtin 包目录（源码模式，可自动发现新插件）；
        PyInstaller 打包后该目录在文件系统中不存在（模块打入 PYZ 归档），
        pkgutil 扫描落空时回退到显式清单（与 qingci-bot-ce.spec 的 hiddenimports 保持一致）。
        """
        from . import builtin

        names: list[str] = []
        try:
            pkg_path = Path(builtin.__path__[0])
            for module_info in pkgutil.iter_modules([str(pkg_path)]):
                if module_info.name.startswith("_"):
                    continue
                names.append(module_info.name)
        except Exception:
            logger.debug("pkgutil 扫描内置插件失败，使用显式清单", exc_info=True)

        if not names:
            names = list(_BUILTIN_PLUGINS)

        for name in names:
            full_path = f"bot.plugin.builtin.{name}"
            try:
                await self._load_or_reload(full_path, bot)
            except Exception:
                logger.exception(f"加载内置插件失败: {name}")

    async def load_external(self, module_path: str, bot) -> bool:
        """加载外部插件"""
        name = module_path.rsplit(".", 1)[-1]
        try:
            await self._load_or_reload(module_path, bot)
            self._load_errors.pop(name, None)
            return True
        except Exception as e:
            self._load_errors[name] = f"{type(e).__name__}: {e}"
            logger.exception(f"加载外部插件失败: {module_path}")
            return False

    async def load_external_dir(self, bot, directory: Path | None = None) -> int:
        """扫描并加载外部插件目录

        支持两种插件形态：
        1. 目录型（推荐）：plugins/<name>/__init__.py，可含 web/、plugin.json
        2. 文件型（兼容）：plugins/<name>.py

        同名的目录型优先于文件型。

        Args:
            bot: Bot 实例
            directory: 插件目录路径，默认为 app_root()/plugins/

        Returns:
            成功加载的插件数量
        """
        if directory is None:
            from ..paths import plugins_dir

            directory = plugins_dir()

        root_str = str(directory.parent)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(f"无法创建插件目录: {directory}，跳过外部插件加载")
            return 0

        # 确保 plugins/__init__.py 存在（目录型和文件型都需要）
        init_file = directory / "__init__.py"
        if not init_file.exists():
            try:
                init_file.write_text(
                    "# Qingci-Bot CE 外部插件目录\n"
                    "# 将插件包（目录）或 .py 文件放入此目录即可自动加载\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.warning(f"无法写入 {init_file}，跳过外部插件加载")
                return 0

        # 收集需要加载的插件名（目录型优先）
        plugins_to_load: list[str] = []
        dir_names: set[str] = set()

        # 1. 扫描目录型插件（含 __init__.py 或 plugin.json 的目录）
        for subdir in sorted(directory.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith("_") or subdir.name.startswith("."):
                continue
            if (subdir / "__init__.py").is_file() or (subdir / "plugin.json").is_file():
                plugins_to_load.append(subdir.name)
                dir_names.add(subdir.name)

        # 2. 扫描文件型插件（未被目录型覆盖的 .py 文件）
        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = py_file.stem
            if module_name not in dir_names:
                plugins_to_load.append(module_name)

        # 加载
        count = 0
        auto_install = bool(getattr(bot.config.bot, "auto_install_plugin_deps", True))
        for module_name in plugins_to_load:
            module_path = f"plugins.{module_name}"
            # 目录型插件：加载前确保其第三方依赖已装到该插件独立的 deps 子目录并注入 sys.path
            plugin_dir = directory / module_name
            if plugin_dir.is_dir() and (plugin_dir / "__init__.py").is_file():
                try:
                    if auto_install:
                        await ensure_dependencies(plugin_dir)
                    else:
                        ensure_in_sys_path(plugin_dir.name)
                except Exception:
                    logger.exception(f"确保插件 {module_name} 依赖失败")
            try:
                ok = await self.load_external(module_path, bot)
                if ok:
                    count += 1
            except Exception as e:
                self._load_errors[module_name] = f"{type(e).__name__}: {e}"
                logger.exception(f"加载外部插件失败: {module_path}")

        if count > 0:
            logger.info(f"从外部插件目录加载了 {count} 个插件")
        return count

    async def _load_or_reload(
        self,
        full_path: str,
        bot,
        replaced_name: str | None = None,
        _loading: set | None = None,
    ) -> None:
        """加载或重载模块，确保模块级装饰器重新执行

        对已缓存的模块使用 reload，对新模块使用 import_module。
        始终包裹 begin/end collection 以收集模块级 Matcher 与 LLM 工具。

        Args:
            replaced_name: reload 场景下被替换插件的原注册名（插件可能改名，
                首次 load 传 None）
            _loading: 正在加载的模块名集合（依赖解析用，检测循环依赖）
        """
        from .llm_tool import begin_tool_collection, end_tool_collection

        collector = begin_module_collection()
        tool_collector = begin_tool_collection()
        sdk_base = _sdk_plugin_base()
        stale_classes: set | None = None
        try:
            if full_path in sys.modules:
                module = sys.modules[full_path]
                stale_classes = set(_module_plugin_classes(module, sdk_base))
                module = importlib.reload(module)
            else:
                module = importlib.import_module(full_path)
        finally:
            end_module_collection()
            end_tool_collection()

        await self._register_from_module(
            module, collector, bot, replaced_name, stale_classes, _loading, tool_collector
        )

    async def _ensure_dependencies(self, plugin: PluginBase, bot, loading: set) -> None:
        """确保插件声明的依赖已加载（支持 PEP 440 版本约束）

        - require 格式: "name" 或 "name>=1.0,<2.0"
        - 依赖已注册时校验版本
        - 未注册时尝试加载 bot.plugin.builtin.<name> 模块
        - 依赖缺失或循环依赖时抛出 ValueError
        """
        for dep in plugin.require or []:
            dep_name, version_spec = _parse_version_spec(dep)

            existing = self._plugins.get(dep_name)
            if existing is not None:
                if version_spec is not None:
                    try:
                        dep_version = Version(existing.version)
                    except InvalidVersion:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但 {dep_name} 版本号 {existing.version} 无效"
                        ) from None
                    if dep_version not in version_spec:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但当前版本为 {existing.version}"
                        )
                continue

            if dep_name in loading:
                chain = " -> ".join([*loading, dep_name])
                raise ValueError(f"插件循环依赖: {chain}")

            dep_module = f"bot.plugin.builtin.{dep_name}"
            try:
                importlib.import_module(dep_module)
            except ImportError:
                raise ValueError(
                    f"插件 {plugin.name} 依赖的插件 {dep_name} 不存在（找不到模块 {dep_module}）"
                ) from None
            await self._load_or_reload(dep_module, bot, _loading=loading)

            # 加载后再次校验版本
            if version_spec is not None:
                dep_plugin = self._plugins.get(dep_name)
                if dep_plugin is not None:
                    try:
                        dep_version = Version(dep_plugin.version)
                    except InvalidVersion:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但 {dep_name} 版本号 {dep_plugin.version} 无效"
                        ) from None
                    if dep_version not in version_spec:
                        raise ValueError(
                            f"插件 {plugin.name} 依赖 {dep_name}{version_spec}，"
                            f"但加载后版本为 {dep_plugin.version}"
                        )

    async def _register_from_module(
        self,
        module,
        collector: list[Matcher],
        bot,
        replaced_name: str | None = None,
        stale_classes: set | None = None,
        _loading: set | None = None,
        tool_collector: list | None = None,
    ) -> None:
        """从模块中查找 PluginBase 子类并注册"""
        from ..paths import data_root

        sdk_base = _sdk_plugin_base()
        plugin_classes = _module_plugin_classes(module, sdk_base)

        if stale_classes is not None:
            plugin_classes = [c for c in plugin_classes if c not in stale_classes]

        if not plugin_classes:
            if replaced_name is not None:
                raise ValueError(
                    f"模块 {module.__name__} 中未找到插件类，无法替换已注册的插件 {replaced_name}"
                )
            return

        if len(plugin_classes) > 1:
            raise ValueError(
                f"模块 {module.__name__} 定义了 {len(plugin_classes)} 个 PluginBase 子类，"
                f"每模块仅允许 1 个"
            )

        plugin_cls = plugin_classes[0]
        plugin = plugin_cls()

        # 独立 SDK 式插件：将 SDK 数据目录重定向到当前实例可写数据根，
        # 保证插件数据（DB/缓存/导出文件）遵循实例隔离，而非落在 SDK 默认目录。
        if sdk_base is not None and issubclass(plugin_cls, sdk_base):
            try:
                import qingci_plugin_sdk.paths as sdk_paths

                sdk_paths.set_data_root(data_root())
            except ImportError:
                logger.debug("qingci_plugin_sdk 未安装，跳过 SDK 数据目录重定向")

        # 依赖解析
        loading = _loading if _loading is not None else set()
        if plugin.name in loading:
            chain = " -> ".join([*loading, plugin.name])
            raise ValueError(f"插件循环依赖: {chain}")
        loading.add(plugin.name)
        try:
            await self._ensure_dependencies(plugin, bot, loading)

            target_name = replaced_name or plugin.name
            old_plugin = self._plugins.get(target_name)

            # 加载插件级配置
            await self._load_plugin_config(plugin, bot)

            # 初始化插件
            await self._init_plugin(plugin, bot)
            if plugin.matchers is None:
                plugin.matchers = []

            # 关联 Matcher
            for m in plugin.matchers:
                if not m.owner:
                    m.owner = plugin.name
            for m in collector:
                handler_mod = getattr(m.handler, "__module__", "") or ""
                if handler_mod and handler_mod != module.__name__:
                    continue
                m.owner = plugin.name
                plugin.matchers.append(m)

            # 展开 on_command 子指令 matcher（详见 _expand_subcommand_matchers）
            _expand_subcommand_matchers(plugin.matchers, plugin.name)

            # 成功：设置状态为 LOADED
            plugin._status = PluginStatus.LOADED

            old_tool_names = self._plugin_tools.get(target_name, [])
            # 注册表替换（卸载旧 + 挂新）全程持写锁：等待在途分发（读锁）
            # 结束，防止分发读到新旧混合的 Matcher / 页面 / 工具注册
            swap_guarded = await self._reload_rw.acquire_write()
            try:
                if old_plugin is not None:
                    await self.unload(target_name)
                existing = self._plugins.get(plugin.name)
                if existing is not None and existing is not old_plugin:
                    logger.warning(
                        f"插件重名覆盖: {plugin.name}（{type(existing).__module__} "
                        f"被 {module.__name__} 替换）"
                    )
                    # 重名覆盖：被覆盖的旧插件需正常卸载，清理其调度任务 / LLM
                    # 工具 / Web 页面注册，避免资源残留成为幽灵实例
                    await self.unload(plugin.name)
                self._plugins[plugin.name] = plugin
                # 注册插件声明的 LLM 工具（模块级 @llm_tool），卸载时注销
                if tool_collector:
                    from .llm_tool import register_tools

                    registered = register_tools(bot.tool_registry, plugin.name, tool_collector)
                    if registered:
                        self._plugin_tools[plugin.name] = registered
                        # 保留 collector，供重载失败时恢复旧插件工具注册
                        plugin._tool_collector = tool_collector
                # 重载场景下 unload 已清除页面注册，需重新收集
                # （若插件没有页面，_plugin_pages 保持为空即可）
                self._collect_plugin_pages(plugin)
                self._collect_plugin_apis(plugin)
            except BaseException:
                try:
                    await plugin.on_unload()
                except (Exception, asyncio.CancelledError):
                    logger.exception(f"插件 {plugin.name} 补偿 on_unload 异常")
                # 新插件注册失败：清理其已注册的资源，并尽力恢复旧插件，
                # 避免旧插件已被卸载、新插件未注册导致插件从管理器消失
                self._plugins.pop(plugin.name, None)
                self._plugin_tools.pop(plugin.name, None)
                self._remove_plugin_pages(plugin.name)
                self._remove_plugin_apis(plugin.name)
                if old_plugin is not None and target_name not in self._plugins:
                    self._plugins[target_name] = old_plugin
                    self._plugin_tools[target_name] = old_tool_names
                    old_collector = getattr(old_plugin, "_tool_collector", None)
                    if old_tool_names and old_collector and plugin.bot is not None:
                        from .llm_tool import register_tools

                        try:
                            register_tools(plugin.bot.tool_registry, target_name, old_collector)
                        except Exception:
                            logger.exception(f"恢复旧插件 {target_name} 工具注册失败")
                    self._invalidate_matchers_cache()
                    logger.warning(f"插件 {plugin.name} 注册失败，已恢复旧插件 {target_name}")
                raise
            finally:
                if swap_guarded:
                    await self._reload_rw.release_write()

            matcher_count = len(plugin.matchers) if plugin.matchers else 0
            logger.info(
                f"插件已加载: {plugin.name} v{plugin.version}"
                f" (matchers: {matcher_count}, category: {plugin.category or '未分类'})"
            )
            self._invalidate_matchers_cache()
        except Exception:
            plugin._status = PluginStatus.ERROR
            raise
        finally:
            loading.discard(plugin.name)

    # ---- 插件级配置 ----

    async def _load_plugin_config(self, plugin: PluginBase, bot) -> None:
        """从 config.yaml 加载插件级配置

        查找路径：plugins.<plugin_name> 节
        若插件定义了 Config 内嵌类，使用 pydantic 校验；
        否则将原始 dict 赋值给 plugin_config。
        """
        config_dict = bot.config.get_plugin_config(plugin.name)
        if config_dict is None:
            return

        # 检查插件是否定义了 Config 内嵌类
        config_cls = getattr(type(plugin), "Config", None)
        if config_cls is not None and isinstance(config_cls, type):
            try:
                plugin.plugin_config = config_cls(**config_dict)
            except Exception as e:
                logger.warning(f"插件 {plugin.name} 配置校验失败: {e}，使用原始 dict")
                plugin.plugin_config = config_dict
        else:
            plugin.plugin_config = config_dict

        # M18：配置更新后通知插件刷新运行期快照（插件可定义 on_config_update
        # 把新配置热应用到自身状态，避免必须重载插件才生效）。
        try:
            hook = getattr(plugin, "on_config_update", None)
            if callable(hook):
                await hook()
        except Exception:
            logger.exception(f"插件 {plugin.name} on_config_update 异常")

    # ---- 卸载 ----

    async def unload(self, name: str) -> None:
        """卸载插件"""
        plugin = self._plugins.pop(name, None)
        if plugin:
            plugin._status = PluginStatus.UNLOADING
            try:
                await plugin.on_unload()
            except Exception:
                logger.exception(f"插件 {name} on_unload 异常")
            scheduler = getattr(plugin.bot, "scheduler", None) if plugin.bot else None
            if scheduler is not None:
                try:
                    scheduler.remove_jobs_by_owner(name)
                except Exception:
                    logger.exception(f"清理插件 {name} 定时任务异常")
            # 清理指标
            self._metrics.pop(name, None)
            # 注销插件注册的 LLM 工具
            tool_names = self._plugin_tools.pop(name, [])
            if tool_names and plugin.bot is not None and getattr(plugin.bot, "tool_registry", None):
                for full_name in tool_names:
                    plugin.bot.tool_registry.unregister(full_name)
            # 清理 Web 管理页面注册
            self._remove_plugin_pages(name)
            # 清理插件级 Web API 注册（已挂载路由保留，请求时动态解析失效）
            self._remove_plugin_apis(name)
            # 清理该插件名下挂起的会话阶梯（多轮交互等待态）
            dispatcher = getattr(plugin.bot, "dispatcher", None) if plugin.bot else None
            if dispatcher is not None:
                try:
                    await dispatcher.clear_steps_for(name)
                except Exception:
                    logger.exception(f"清理插件 {name} 会话阶梯异常")
            logger.info(f"插件已卸载: {name}")
        # 清理加载错误记录（已卸载则不再展示 error 状态幽灵条目）
        self._load_errors.pop(name, None)
        self._invalidate_matchers_cache()

    async def remove(self, name: str, *, purge: bool = False) -> None:
        """卸载并删除插件

        purge=False（默认）：卸载 + 删除插件代码目录，保留数据与依赖
        （便于重装）。实例模式下代码目录与数据目录重合
        （instances/<inst>/plugins/<name>），删除代码目录会连带删除插件数据，
        为避免数据丢失，重合时默认仅卸载不删文件（P1 修复，见插件卸载评估报告）。
        purge=True：彻底删除——卸载 + 删除代码目录、独立数据目录
        （data_root()/plugins/<name>）与第三方依赖（deps/、安装标记、sys.path 注入）。
        """
        await self.unload(name)
        from ..paths import data_root, plugins_dir

        base = plugins_dir()
        # 兜底校验：解析后必须位于插件目录内（防插件声明恶意 name 越界删除）
        plugin_dir = (base / name).resolve()
        if not plugin_dir.is_relative_to(base.resolve()):
            raise ValueError(f"非法插件删除路径: {name!r}")

        data_dir = (data_root() / "plugins" / name).resolve()
        # 实例模式下 plugins_dir 与 data_root()/plugins 重合 → 代码目录即数据目录
        code_and_data_merge = plugin_dir == data_dir

        import shutil

        def _rmtree(path: Path, label: str) -> None:
            if not path.exists():
                return
            if not path.is_dir():
                logger.warning(f"插件 {name} {label}路径非目录，跳过删除: {path}")
                return
            try:
                shutil.rmtree(path)
            except OSError as e:
                raise RuntimeError(f"插件 {name} {label}删除失败（文件可能被占用）: {path}") from e

        if purge:
            _rmtree(plugin_dir, "代码/数据目录")
            if not code_and_data_merge:
                _rmtree(data_dir, "数据目录")
            from .deps import cleanup_dependencies

            try:
                cleanup_dependencies(name)
            except Exception:
                logger.exception(f"清理插件 {name} 依赖异常")
            logger.info(f"插件已彻底删除: {name} (purge)")
            return

        if code_and_data_merge:
            # 实例模式：默认删除即删数据，仅卸载保留文件，彻底删除需 purge
            logger.info(
                f"插件 {name} 实例模式下代码与数据同目录，仅卸载不删文件"
                "（如需彻底删除请使用 purge）"
            )
            return
        _rmtree(plugin_dir, "代码目录")
        logger.info(f"插件已删除: {name} ({plugin_dir})")

    # ---- 禁用/启用 ----

    async def disable(self, name: str) -> None:
        """禁用插件：跳过事件分发，保留实例（不触发 on_unload）"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"禁用失败：插件 {name} 不存在")
            return
        if plugin.status == PluginStatus.DISABLED:
            logger.info(f"插件 {name} 已处于禁用状态，跳过")
            return
        if plugin.status != PluginStatus.LOADED:
            logger.warning(f"插件 {name} 状态为 {plugin.status.value}，无法禁用")
            return
        plugin._status = PluginStatus.DISABLED
        try:
            await plugin.on_disable()
        except Exception:
            logger.exception(f"插件 {name} on_disable 异常")
        # 禁用时清除该插件名下挂起的会话阶梯（多轮交互等待态不再续接）
        dispatcher = getattr(plugin.bot, "dispatcher", None) if plugin.bot else None
        if dispatcher is not None:
            try:
                await dispatcher.clear_steps_for(name)
            except Exception:
                logger.exception(f"清理插件 {name} 会话阶梯异常")
        self._invalidate_matchers_cache()
        logger.info(f"插件已禁用: {name}")

    async def enable(self, name: str) -> None:
        """启用插件：恢复事件分发（不触发 on_load）"""
        plugin = self._plugins.get(name)
        if not plugin:
            logger.warning(f"启用失败：插件 {name} 不存在")
            return
        if plugin.status == PluginStatus.LOADED:
            logger.info(f"插件 {name} 已处于启用状态，跳过")
            return
        if plugin.status != PluginStatus.DISABLED:
            logger.warning(f"插件 {name} 状态为 {plugin.status.value}，无法启用")
            return
        plugin._status = PluginStatus.LOADED
        try:
            await plugin.on_enable()
        except Exception:
            logger.exception(f"插件 {name} on_enable 异常")
        self._invalidate_matchers_cache()
        logger.info(f"插件已启用: {name}")

    def is_enabled(self, name: str) -> bool:
        """查询插件是否启用（不存在返回 False）"""
        plugin = self._plugins.get(name)
        return plugin.enabled if plugin else False

    # ---- 重载 ----

    async def reload(self, name: str, bot) -> None:
        """重载插件（_reload_lock 串行化，避免并发 reload 竞态）

        重载全程持有读写屏障写锁：等待在途事件分发（读锁）结束后
        才执行模块 reload + 注册表替换，防止分发读到半新半旧状态。
        """
        async with self._reload_lock:
            plugin = self._plugins.get(name)
            if not plugin:
                logger.warning(f"重载失败：插件 {name} 不存在")
                return

            module_path = type(plugin).__module__
            # 同任务重入（handler 内触发 reload）时 acquire_write 返回 False，
            # 此时跳过屏障（读锁仍由本任务持有，注册表替换与分发为同一任务串行）
            guarded = await self._reload_rw.acquire_write()
            try:
                await self._load_or_reload(module_path, bot, replaced_name=name)
            except Exception as e:
                # 记录加载错误供 WebUI 展示（reload 失败时实例状态与磁盘/模块脱节）
                self._load_errors[name] = f"{type(e).__name__}: {e}"
                logger.exception(f"重载插件 {name} 失败，旧插件保持生效")
                raise
            finally:
                if guarded:
                    await self._reload_rw.release_write()
                self._invalidate_matchers_cache()

    # ---- 初始化 ----

    async def _init_plugin(self, plugin: PluginBase, bot) -> None:
        """初始化插件依赖并调用 on_load

        优先使用 DI 容器自动注入（按类型匹配），
        兼容手动赋值（确保向后兼容）。
        """
        # 先用 DI 容器自动注入（按类型注解匹配）
        if hasattr(bot, "di") and bot.di is not None:
            await bot.di.inject(plugin, skip_missing=True)

        # 手动赋值兜底（确保 DI 未覆盖的字段也有值）
        plugin.bot = plugin.bot or bot
        plugin.db = plugin.db or bot.db
        plugin.config = plugin.config or bot.config
        plugin.connection = plugin.connection or bot.connection
        plugin.llm = plugin.llm or bot.llm
        plugin.scheduler = plugin.scheduler or bot.scheduler
        plugin.tool_registry = plugin.tool_registry or bot.tool_registry
        plugin.knowledge_store = plugin.knowledge_store or bot.knowledge_store
        plugin.session_state = plugin.session_state or bot.session_state
        plugin.event_bus = plugin.event_bus or getattr(bot, "event_bus", None)
        plugin.matchers = plugin.matchers or []
        plugin._status = PluginStatus.LOADING
        await plugin.on_load()
        # 收集插件注册的 Web 管理页面
        self._collect_plugin_pages(plugin)
        # 收集插件注册的 Web API（挂载到 /api/plugin-web/<name>/）
        self._collect_plugin_apis(plugin)
        # 加载插件 i18n 翻译资源
        self._load_i18n(plugin)

    # ---- 关闭 ----

    async def shutdown(self) -> None:
        """关闭所有插件"""
        for name in list(self._plugins.keys()):
            await self.unload(name)

    # ---- 全局生命周期钩子 ----

    async def dispatch_lifecycle(self, hook: str, *args, **kwargs) -> None:
        """向所有已加载插件分发全局生命周期钩子（异常隔离）

        仅调用实际覆写了该钩子的插件（跳过基类空实现），
        避免空转。支持 on_startup / on_shutdown / on_bot_connect / on_metaevent。
        """
        base = getattr(PluginBase, hook, None)
        for plugin in list(self._plugins.values()):
            if plugin.status != PluginStatus.LOADED:
                continue
            fn = getattr(type(plugin), hook, None)
            if fn is None or fn is base:
                continue
            try:
                res = getattr(plugin, hook)(*args, **kwargs)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                logger.exception(f"插件 {plugin.name} {hook} 钩子异常")

    # ---- 在线安装 ----

    async def install(
        self,
        bot,
        source: str,
        *,
        name: str | None = None,
        allow_local: bool = False,
        expected_sha256: str = "",
    ) -> bool:
        """在线安装插件到外部插件目录并加载

        source 支持：
        - git 仓库地址（git+https://... / https://... / git@...）
        - HTTP 指向 zip/tar 归档的 URL
        - 本地路径（目录或归档文件，仅 allow_local=True 时接受）

        安装过程：拉取到 plugins/<name>/ → 加载 requirements.txt 依赖 → 加载插件。

        Args:
            bot: Bot 实例（用于加载）
            source: 插件来源
            name: 目标插件名（为空时从来源推断）
            allow_local: 是否接受本地路径来源。市场安装来源由远端索引下发，
                必须保持 False（禁止本地路径），防止恶意索引复制服务器任意目录。
            expected_sha256: 归档校验和（仅 HTTP 归档来源生效；git 自带完整性）
        """
        from ..paths import plugins_dir

        plugin_dir = plugins_dir()
        plugin_dir.mkdir(parents=True, exist_ok=True)

        target_name, target_dir = await self._fetch_plugin(
            source,
            name,
            plugin_dir,
            allow_local=allow_local,
            expected_sha256=expected_sha256,
        )
        if target_dir is None:
            return False

        # 自动安装依赖（requirements.txt / plugin.json 的 requirements 字段，
        # 装入该插件独立的 deps/<name>/ 子目录而非全局环境）
        await ensure_dependencies(target_dir)

        # 确保 plugins 父目录在 sys.path（与 load_external_dir 一致），
        # 避免 EXE 首次安装（目录新建、此前未扫描）时 import 失败
        root_str = str(plugin_dir.parent)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        # 加载插件
        module_path = f"plugins.{target_name}"
        try:
            return await self.load_external(module_path, bot)
        except Exception:
            logger.exception(f"安装后加载插件失败: {module_path}")
            return False

    async def _fetch_plugin(
        self,
        source: str,
        name: str | None,
        plugins_dir: Path,
        allow_local: bool = False,
        expected_sha256: str = "",
    ) -> tuple[str, Path | None]:
        """拉取插件源码到 plugins/ 目录，返回 (插件名, 插件目录)"""
        import shutil
        import tempfile

        source = source.strip()
        is_local_path = Path(source).exists()
        temp_dir: str | None = None
        try:
            if is_local_path:
                if not allow_local:
                    logger.error(f"拒绝本地路径插件来源（远程安装仅允许 http(s)/git）: {source}")
                    return "", None
                src = Path(source).resolve()
                if src.is_dir():
                    return await self._copy_plugin_dir(src, name, plugins_dir)
                if src.is_file() and self._is_archive(src):
                    temp_dir = tempfile.mkdtemp(prefix="qb-plugin-")
                    extracted = await self._extract_archive(src, Path(temp_dir))
                    return await self._copy_plugin_dir(extracted, name, plugins_dir)
                logger.error(f"本地插件来源无效: {source}")
                return "", None
            else:
                # 远程来源：git 克隆或 HTTP 下载归档
                temp_dir = tempfile.mkdtemp(prefix="qb-plugin-")
                staging = Path(temp_dir) / "src"
                # git 仓库识别：git+ / git@ / ssh:// 前缀，或 .git 结尾的 http(s) URL
                is_git = source.startswith(("git+", "git@", "ssh://")) or (
                    source.startswith(("http://", "https://"))
                    and source.rstrip("/").endswith(".git")
                )
                if is_git:
                    from .ssrf import is_allowed_git_url, is_private_url

                    repo = source[4:] if source.startswith("git+") else source
                    # 与 _download_archive 同基线：git 来源同样校验协议白名单
                    # 与私网地址，防止恶意索引借 git+file:// 或内网 SSH 绕过防护
                    if not is_allowed_git_url(repo) or is_private_url(repo):
                        logger.error(f"拒绝不安全的 git 来源: {source}")
                        return "", None
                    ok = await self._run_subprocess(
                        ["git", "clone", "--depth", "1", repo, str(staging)]
                    )
                    if not ok or not staging.exists():
                        logger.error(f"git 克隆失败: {source}")
                        return "", None
                elif source.startswith(("http://", "https://")):
                    ok = await self._download_archive(source, staging)
                    if not ok:
                        logger.error(f"下载归档失败: {source}")
                        return "", None
                    # 归档完整性校验：市场索引声明 source_sha256 时校验下载内容，
                    # 防传输被篡改/投毒（git 来源跳过，git 自带内容完整性）
                    if expected_sha256:
                        actual = await asyncio.to_thread(_sha256_file, staging)
                        if actual != expected_sha256:
                            logger.error(f"插件归档校验失败（sha256 不匹配，可能被篡改）: {source}")
                            return "", None
                    staging = await self._extract_archive(staging, staging.parent)
                else:
                    logger.error(f"不支持的插件来源: {source}")
                    return "", None

                # 找到插件目录（仓库根或其下含 plugin.json/__init__.py 的目录；
                # 指定 name 时优先精确匹配，兼容 plugins/<name>/ 嵌套布局）
                plugin_dir = self._locate_plugin_dir(staging, name)
                if plugin_dir is None:
                    logger.error(f"来源中未找到插件目录: {source}")
                    return "", None
                return await self._copy_plugin_dir(plugin_dir, name, plugins_dir)
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def _copy_plugin_dir(
        self, src: Path, name: str | None, plugins_dir: Path
    ) -> tuple[str, Path | None]:
        """复制插件目录到 plugins/，返回 (插件名, 目标目录)

        先复制到 staging 目录再原子替换，失败时保留旧版本插件目录。
        """
        import shutil
        import uuid

        target_name = name or src.name
        # 插件名合法性校验：市场索引/仓库目录名可能被篡改（路径穿越）
        if not _is_valid_plugin_name(target_name):
            logger.error(f"非法插件名，拒绝安装: {target_name!r}")
            return "", None
        target = plugins_dir / target_name
        # 先复制到 staging，成功后原子替换（同目录 rename），失败保留旧版本
        staging = plugins_dir / f".staging-{target_name}-{uuid.uuid4().hex[:8]}"
        try:
            shutil.copytree(
                src, staging, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv")
            )
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
            return target_name, target
        except OSError as e:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            logger.error(f"复制插件目录失败: {e}")
            return "", None

    @staticmethod
    async def _run_subprocess(cmd: list[str]) -> bool:
        """运行子进程并返回是否成功（communicate 带超时，防止永久挂起）"""

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=NO_WINDOW_FLAG,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                logger.error(f"子进程超时（{cmd[0]}），已终止")
                return False
            if proc.returncode != 0:
                logger.error(f"子进程失败 ({cmd[0]}): {stdout.decode(errors='replace')[-2000:]}")
                return False
            return True
        except (OSError, ValueError) as e:
            logger.error(f"无法运行子进程 {cmd[0]}: {e}")
            return False

    @staticmethod
    async def _download_archive(url: str, dest: Path) -> bool:
        """下载远程归档文件到 dest（同步 urllib 放入线程池，避免阻塞事件循环）"""
        import urllib.request

        from .ssrf import NoRedirectHandler, is_private_url

        # 来源可能由远端市场索引下发：拦截私网/环回地址，防止 SSRF
        if is_private_url(url):
            logger.error(f"拒绝下载私网/环回地址的归档: {url}")
            return False

        def _sync_download() -> bool:
            try:
                # 禁止跟随重定向，防止 302 跳转到内网地址
                opener = urllib.request.build_opener(NoRedirectHandler)
                req = urllib.request.Request(url, headers={"User-Agent": "Qingci-Bot-CE"})
                with opener.open(req, timeout=60) as resp:
                    # 归档大小上限（防 zip 炸弹撑爆磁盘；Content-Length 缺失时边下边限）
                    declared = resp.headers.get("Content-Length")
                    if declared and int(declared) > _MAX_ARCHIVE_BYTES:
                        logger.error(f"归档声明过大，拒绝下载: {url} ({declared} bytes)")
                        return False
                    remaining = _MAX_ARCHIVE_BYTES
                    with open(dest, "wb") as f:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            if remaining < 0:
                                logger.error(
                                    f"归档超过大小上限（{_MAX_ARCHIVE_BYTES}），中止下载: {url}"
                                )
                                return False
                            f.write(chunk)
                return True
            except (OSError, urllib.error.URLError, ValueError) as e:
                logger.error(f"下载失败 {url}: {e}")
                return False

        return await asyncio.to_thread(_sync_download)

    @staticmethod
    async def _extract_archive(archive: Path, dest_dir: Path) -> Path:
        """解压 zip/tar 归档，返回解压后的根目录

        无法按扩展名判断格式时嗅探文件头（HTTP 下载的暂存文件通常无扩展名）。
        """
        import stat
        import tarfile
        import zipfile

        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = archive.name.lower()
        fmt = "zip" if fname.endswith(".zip") else None
        if fmt is None and fname.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
            fmt = "tar"
        if fmt is None:
            fmt = _sniff_archive_format(archive)
        if fmt == "zip":
            with zipfile.ZipFile(archive) as zf:
                # 解压总量上限（防 zip 炸弹）
                if sum(i.file_size for i in zf.infolist()) > _MAX_EXTRACT_BYTES:
                    raise ValueError("归档解压后过大，已拒绝")
                # 安全解压：与下方 tar 分支同规则，预检每个成员路径，
                # 拒绝绝对路径（含盘符）与 .. 穿越条目，防止 Zip Slip；
                # 同时拒绝符号链接条目（防 symlink 逃逸覆写目录外文件）
                dest_root = dest_dir.resolve()
                for info in zf.infolist():
                    member = info.filename.replace("\\", "/")
                    member_target = (dest_root / member).resolve()
                    if not member_target.is_relative_to(dest_root):
                        raise ValueError(f"归档包含非法路径: {info.filename}")
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise ValueError(f"归档包含符号链接，已拒绝: {info.filename}")
                zf.extractall(dest_dir)
        elif fmt == "tar":
            with tarfile.open(archive) as tf:
                # 解压总量上限（防 zip 炸弹）
                if sum(m.size for m in tf.getmembers()) > _MAX_EXTRACT_BYTES:
                    raise ValueError("归档解压后过大，已拒绝")
                # 安全解压：Py3.12+ 用 data_filter；旧版本手动预检成员路径，
                # 拒绝绝对路径与 .. 成员，并拒绝 symlink/hardlink 成员——
                # 3.10/3.11 下预检只查文本路径，symlink 可指向解压目录外
                # 再经后续成员写入实现逃逸（Zip Slip 变体）
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(dest_dir, filter="data")
                else:
                    dest_root = dest_dir.resolve()
                    for m in tf.getmembers():
                        if m.issym() or m.islnk():
                            raise ValueError(f"归档包含符号链接，已拒绝: {m.name}")
                        member_target = (dest_root / m.name).resolve()
                        if not member_target.is_relative_to(dest_root):
                            raise ValueError(f"归档包含非法路径: {m.name}")
                    tf.extractall(dest_dir)
        else:
            raise ValueError(f"不支持的归档格式: {fname}")
        # 若顶层为单个目录，返回该目录
        entries = [p for p in dest_dir.iterdir() if not p.name.startswith("__")]
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return dest_dir

    @staticmethod
    def _locate_plugin_dir(root: Path, name: str | None = None) -> Path | None:
        """定位仓库/归档中的插件目录

        1. 根目录本身是插件（含 plugin.json 或 __init__.py）→ 返回根
        2. 指定 name 时：递归（最多 2 层）查找目录名 == name 且是插件的目录
        3. 否则：返回首个直接子目录中的插件
        4. 直接子目录中找不到时，再向下找一层（兼容 plugins/<name>/ 嵌套）
        """
        if (root / "plugin.json").is_file() or (root / "__init__.py").is_file():
            return root

        def _is_plugin_dir(d: Path) -> bool:
            return (d / "plugin.json").is_file() or (d / "__init__.py").is_file()

        if name:
            for pattern in (f"*/{name}", f"*/*/{name}"):
                for child in root.glob(pattern):
                    if child.is_dir() and _is_plugin_dir(child):
                        return child

        for child in sorted(root.iterdir()):
            if child.is_dir() and _is_plugin_dir(child):
                return child

        # 嵌套一层：兼容 plugins/<name>/ 布局
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            for sub in sorted(child.iterdir()):
                if sub.is_dir() and _is_plugin_dir(sub):
                    return sub
        return None

    @staticmethod
    def _is_archive(path: Path) -> bool:
        return path.suffix.lower() in (".zip", ".tar", ".gz", ".tgz", ".bz2")

    # ---- 工具 ----

    def remove_temp_matcher(self, matcher: Matcher) -> None:
        """移除一次性（temp）匹配器并失效缓存"""
        if not getattr(matcher, "owner", ""):
            return
        plugin = self._plugins.get(matcher.owner)
        if plugin is not None and plugin.matchers and matcher in plugin.matchers:
            plugin.matchers.remove(matcher)
            self._invalidate_matchers_cache()


def _is_secret_field(name: str) -> bool:
    """M19：判断配置字段是否敏感（token/secret/password/api_key 等），用于脱敏与空值保护。"""
    lowered = str(name).casefold()
    return any(hint in lowered for hint in ("token", "secret", "password", "api_key", "apikey"))


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
