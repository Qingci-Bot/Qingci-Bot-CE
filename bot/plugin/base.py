"""插件基类"""

import enum
from abc import ABC, abstractmethod
from typing import Any, Optional, TYPE_CHECKING

from ..core.dispatcher import MessageContext

if TYPE_CHECKING:
    from ..core.bot import QingciBot
    from ..core.connection import OneBotConnection
    from ..config import ConfigManager
    from ..db.database import Database
    from ..llm.manager import LLMManager
    from .matcher import Matcher


class PluginStatus(str, enum.Enum):
    """插件状态枚举"""
    LOADING = "loading"       # 正在加载（on_load 执行中）
    LOADED = "loaded"         # 已加载，正常运行
    DISABLED = "disabled"     # 已禁用，跳过事件分发
    ERROR = "error"           # 加载/运行出错
    UNLOADING = "unloading"   # 正在卸载（on_unload 执行中）


class PluginBase(ABC):
    """插件基类

    支持两种消息处理方式：
    1. 旧式：重写 on_message(ctx) -> Optional[str]
    2. 新式：在 on_load 中注册 Matcher（self.matchers.append(on_command(...)(handler))）
       或用模块级装饰器 @on_command(...)（PluginManager 自动收集）

    新旧方式可共存，Dispatcher 按优先级统一调度。

    插件级配置：
    - 定义 Config 内嵌类（pydantic BaseModel 风格）声明配置项
    - 框架自动从 config.yaml 的 plugins.<name> 节加载到 self.plugin_config

    插件导出：
    - on_load 中调用 self.export("key", value) 暴露接口
    - 依赖方通过 self.get_exports("plugin_name") 获取导出字典

    状态/生命周期：
    - LOADING → LOADED → DISABLED ↔ LOADED → UNLOADING
    - LOADING → ERROR（加载失败）
    - 禁用/启用不触发 on_load/on_unload
    """

    # 插件元信息
    name: str = ""
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    category: str = ""  # 插件分类：chat / admin / tool / fun / 自定义
    # 依赖的插件 name 列表：支持 PEP 440 版本约束，如 "chat>=1.0,<2.0"
    # 依赖缺失或形成循环依赖时插件加载失败（借鉴 NoneBot2 require 机制）
    require: list[str] = []

    # 插件状态（由 PluginManager 管理）
    _status: PluginStatus = PluginStatus.LOADING

    # 依赖引用（由 PluginManager 注入）
    bot: Optional["QingciBot"] = None
    db: Optional["Database"] = None
    config: Optional["ConfigManager"] = None
    connection: Optional["OneBotConnection"] = None
    llm: Optional["LLMManager"] = None

    # 可选依赖引用（由 PluginManager 注入，允许为 None）
    scheduler: Optional[Any] = None
    tool_registry: Optional[Any] = None
    knowledge_store: Optional[Any] = None
    session_state: Optional[Any] = None  # TTL 会话状态存储

    # Matcher 列表（由 PluginManager 初始化，新式插件在 on_load 中填充）
    matchers: Optional[list["Matcher"]] = None

    # 插件级配置（由 PluginManager 从 config.yaml 加载）
    plugin_config: Optional[Any] = None

    # 导出注册表（插件间服务接口）
    _exports: dict[str, Any]

    # 中间件链（per-handler 钩子）
    # before_handler: async (matcher, ctx) -> Optional[str]
    #   - 返回非 None 时拦截，跳过 handler 并将返回值作为回复
    # after_handler: async (matcher, ctx, result) -> Optional[str]
    #   - 可修改/替换 handler 返回值
    _before_handlers: list[Any]
    _after_handlers: list[Any]

    def __init__(self):
        self._exports = {}
        self._before_handlers = []
        self._after_handlers = []
        self._status = PluginStatus.LOADING

    # ---- 状态 ----

    @property
    def status(self) -> PluginStatus:
        """插件当前状态"""
        return self._status

    @property
    def enabled(self) -> bool:
        """向后兼容：LOADED 视为启用（含 LOADING 过渡态）"""
        return self._status in (PluginStatus.LOADING, PluginStatus.LOADED)

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """向后兼容：setter 由 PluginManager.disable/enable 使用"""
        if value:
            if self._status == PluginStatus.DISABLED:
                self._status = PluginStatus.LOADED
        else:
            if self._status == PluginStatus.LOADED:
                self._status = PluginStatus.DISABLED

    # ---- 导出机制 ----

    def export(self, key: str, value: Any) -> None:
        """导出接口供依赖方调用（在 on_load 中使用）"""
        self._exports[key] = value

    def get_exports(self, plugin_name: str) -> dict[str, Any]:
        """获取依赖插件的导出字典（需在 on_load 中声明 require）

        注意：方法名为 get_exports，避免与 require 依赖声明属性重名。
        """
        if self.bot is None:
            raise RuntimeError("插件未初始化，无法获取依赖")
        dep = self.bot.plugin_manager.get(plugin_name)
        if dep is None:
            raise RuntimeError(f"依赖插件 {plugin_name} 未加载")
        return dep._exports

    # ---- 中间件 ----

    def register_before(self, fn) -> None:
        """注册 handler 前置钩子：async (matcher, ctx) -> Optional[str]"""
        if fn not in self._before_handlers:
            self._before_handlers.append(fn)

    def register_after(self, fn) -> None:
        """注册 handler 后置钩子：async (matcher, ctx, result) -> Optional[str]"""
        if fn not in self._after_handlers:
            self._after_handlers.append(fn)

    # ---- 生命周期 ----

    @abstractmethod
    async def on_load(self):
        """插件加载时调用"""
        ...

    @abstractmethod
    async def on_unload(self):
        """插件卸载时调用"""
        ...

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        """处理消息事件，返回回复文本或 None"""
        return None

    async def on_notice(self, event: dict) -> None:
        """处理通知事件"""
        pass

    async def on_request(self, event: dict) -> Optional[bool]:
        """处理请求事件（加群/加好友），返回 True 同意 / False 拒绝 / None 忽略"""
        return None

    async def on_disable(self):
        """插件被禁用时调用（可选覆写，用于停用定时任务等轻量清理）"""
        pass

    async def on_enable(self):
        """插件被启用时调用（可选覆写，用于恢复定时任务等）"""
        pass