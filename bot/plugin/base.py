"""插件基类"""

from abc import ABC, abstractmethod
from typing import Optional

from ..core.dispatcher import MessageContext


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

    # 依赖引用（由 PluginManager 注入）
    bot = None       # Bot 实例
    db = None        # Database 实例
    config = None    # ConfigManager 实例
    connection = None  # OneBotConnection 实例
    llm = None       # LLMManager 实例

    # Matcher 列表（由 PluginManager 初始化，新式插件在 on_load 中填充）
    matchers: list = None  # type: list  # list[Matcher]

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