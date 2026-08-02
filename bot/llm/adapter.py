"""LLM 适配器基类

定义统一的 LLM 调用接口，支持：
- 同步 / 流式文本对话
- 多模态（图片）输入
- Function Calling（tools）
- 可用性检查
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional


class LLMAdapter(ABC):
    """LLM 适配器抽象基类"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        tools: Optional[list[dict]] = None,
        images: Optional[list[str]] = None,
        **kwargs,
    ) -> str:
        """同步聊天（返回完整回复文本）

        Args:
            messages: OpenAI 格式的消息列表
            system_prompt: 系统提示词，若提供则前置到 messages
            max_tokens: 单次回复最大 token
            temperature: 采样温度
            tools: Function Calling 工具定义（OpenAI tools 格式）
            images: 图片列表，每项为 URL 或 base64 data URI
        """
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
        """流式聊天（逐段返回增量文本）"""
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

    # ============ 便捷工具方法 ============

    @staticmethod
    def build_image_message(text: str, images: list[str]) -> dict:
        """构建多模态消息（OpenAI content 数组格式）"""
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})
        return {"role": "user", "content": content}
