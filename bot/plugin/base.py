"""插件基类"""

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


class PluginBase(ABC):
    """插件基类

    支持两种消息处理方式：
    1. 旧式：重写 on_message(ctx) -> Optional[str]
    2. 新式：在 on_load 中注册 Matcher（self.matchers.append(on_command(...)(handler))）
       或用模块级装饰器 @on_command(...)（PluginManager 自动收集）

    新旧方式可共存，Dispatcher 按优先级统一调度。
    """

    # 插件元信息
    name: str = ""
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    # 依赖的插件 name 列表：加载前自动先加载依赖插件，
    # 依赖缺失或形成循环依赖时插件加载失败（借鉴 NoneBot2 require 机制）
    require: list[str] = []

    # 依赖引用（由 PluginManager 注入）
    bot: Optional["QingciBot"] = None
    db: Optional["Database"] = None
    config: Optional["ConfigManager"] = None
    connection: Optional["OneBotConnection"] = None
    llm: Optional["LLMManager"] = None

    # 可选依赖引用（由 PluginManager 注入，允许为 None）
    # 定时任务调度器（批次 1：BotScheduler 实例，随 bot 启停）
    scheduler: Optional[Any] = None
    # Function Calling 工具注册表（批次 3 创建，未启用时为 None）
    tool_registry: Optional[Any] = None
    # 知识库向量存储（批次 3 创建，未启用时为 None）
    knowledge_store: Optional[Any] = None

    # Matcher 列表（由 PluginManager 初始化，新式插件在 on_load 中填充）
    matchers: Optional[list["Matcher"]] = None  # 实际值由 PluginManager._init_plugin 设置为 list

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