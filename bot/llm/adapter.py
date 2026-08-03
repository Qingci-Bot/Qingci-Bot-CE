"""LLM 适配器基类

定义统一的 LLM 调用接口，支持：
- 同步 / 流式文本对话
- 多模态（图片）输入
- Function Calling（tools）
- 可用性检查
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional


@dataclass
class ChatResult:
    """chat_detail 的完整返回结果

    Attributes:
        content: 回复文本（模型未返回文本时为 ""）
        usage: token 用量信息，形如 {"prompt_tokens": int, "completion_tokens": int}，
            服务未提供时为 None
        tool_calls: 模型返回的 tool_calls 原始列表（OpenAI tools 格式），
            未调用工具时为 None
    """
    content: str
    usage: Optional[dict] = None
    tool_calls: Optional[list] = None


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
    async def chat_detail(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        tools: Optional[list[dict]] = None,
        images: Optional[list[str]] = None,
        **kwargs,
    ) -> ChatResult:
        """同步聊天（返回完整结果，含 usage 与 tool_calls）

        与 chat() 参数完全一致，但返回 ChatResult 而非纯文本：
        - content 为回复文本
        - usage 为 token 用量（用于用量统计）
        - tool_calls 为工具调用原始列表（用于 Function Calling 循环）

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
    def chat_stream(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[str]:
        """流式聊天（逐段返回增量文本）

        契约：子类必须实现为异步生成器（async generator），
        返回 AsyncIterator[str]，调用方直接用 ``async for`` 迭代，
        例如：``async for chunk in adapter.chat_stream(...): ...``。
        注意本方法不能定义为 ``async def`` 普通方法（那会返回协程而非迭代器）。
        """
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
