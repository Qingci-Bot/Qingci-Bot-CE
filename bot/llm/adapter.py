"""LLM 适配器基类"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class LLMAdapter(ABC):
    """LLM 适配器抽象基类"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        """同步聊天（返回完整回复）"""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[str]:
        """流式聊天（返回增量文本）"""
        ...

    @abstractmethod
    async def check_availability(self) -> bool:
        """检查 LLM 服务是否可用"""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供商名称"""
        ...

    @abstractmethod
    async def close(self):
        """关闭适配器资源"""
        ...