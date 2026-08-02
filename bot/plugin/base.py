"""插件基类"""

from abc import ABC, abstractmethod
from typing import Optional

from ..core.dispatcher import MessageContext


class PluginBase(ABC):
    """插件基类"""

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