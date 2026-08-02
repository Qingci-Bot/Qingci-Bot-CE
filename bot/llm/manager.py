"""LLM 管理器 - 多模型管理、上下文维护"""

import logging
from typing import AsyncIterator, Optional

from ..config import LLMConfig
from .adapter import LLMAdapter
from .openai import OpenAIAdapter

logger = logging.getLogger("qingci-bot.llm.manager")


class LLMManager:
    """LLM 管理器：适配器管理、会话上下文"""

    def __init__(self, config: LLMConfig):
        self._config = config
        self._adapter: Optional[LLMAdapter] = None
        # 会话历史: key = "group:{group_id}:{user_id}" 或 "private:{user_id}"
        self._sessions: dict[str, list[dict]] = {}

    def _create_adapter(self) -> LLMAdapter:
        """根据配置创建适配器"""
        provider = self._config.provider
        if provider in ("openai", "deepseek", "ollama", "custom"):
            return OpenAIAdapter(
                api_url=self._config.api_url,
                api_key=self._config.api_key,
                model=self._config.model,
            )
        raise ValueError(f"不支持的 LLM provider: {provider}")

    @property
    def adapter(self) -> LLMAdapter:
        if self._adapter is None:
            self._adapter = self._create_adapter()
        return self._adapter

    async def reload(self, config: LLMConfig):
        """重新加载配置并重置适配器"""
        self._config = config
        await self.close()

    async def close(self):
        """关闭适配器，释放资源"""
        if self._adapter:
            try:
                await self._adapter.close()
            except Exception:
                pass
            self._adapter = None

    def _session_key(self, message_type: str, group_id: int, user_id: int) -> str:
        if message_type == "private":
            return f"private:{user_id}"
        return f"group:{group_id}:{user_id}"

    def clear_session(self, message_type: str = "", group_id: int = 0, user_id: int = 0):
        """清除会话历史"""
        if message_type and user_id:
            key = self._session_key(message_type, group_id, user_id)
            self._sessions.pop(key, None)
        else:
            self._sessions.clear()

    async def chat(
        self,
        message: str,
        message_type: str = "group",
        group_id: int = 0,
        user_id: int = 0,
        user_name: str = "",
    ) -> str:
        """同步聊天"""
        key = self._session_key(message_type, group_id, user_id)
        if key not in self._sessions:
            self._sessions[key] = []

        # 添加用户消息
        self._sessions[key].append({"role": "user", "content": message})

        # 裁剪历史
        max_history = self._config.max_history * 2  # 每轮 = user + assistant
        if len(self._sessions[key]) > max_history:
            self._sessions[key] = self._sessions[key][-max_history:]

        # 调用 LLM
        try:
            reply = await self.adapter.chat(
                messages=self._sessions[key],
                system_prompt=self._config.system_prompt,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            # 移除刚加入的用户消息，保持历史一致性
            if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                self._sessions[key].pop()
            return "抱歉，AI 服务暂时不可用，请稍后再试。"

        # 保存助手回复
        self._sessions[key].append({"role": "assistant", "content": reply})
        return reply

    async def chat_stream(
        self,
        message: str,
        message_type: str = "group",
        group_id: int = 0,
        user_id: int = 0,
    ) -> AsyncIterator[str]:
        """流式聊天"""
        key = self._session_key(message_type, group_id, user_id)
        if key not in self._sessions:
            self._sessions[key] = []

        self._sessions[key].append({"role": "user", "content": message})

        max_history = self._config.max_history * 2
        if len(self._sessions[key]) > max_history:
            self._sessions[key] = self._sessions[key][-max_history:]

        full_reply = ""
        try:
            async for chunk in self.adapter.chat_stream(
                messages=self._sessions[key],
                system_prompt=self._config.system_prompt,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            ):
                full_reply += chunk
                yield chunk
            # 仅在成功完成时保存助手回复
            if full_reply:
                self._sessions[key].append({"role": "assistant", "content": full_reply})
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            # 失败时移除用户消息，保持历史一致性
            if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                self._sessions[key].pop()
            yield "抱歉，AI 服务暂时不可用。"

    async def check_availability(self) -> bool:
        try:
            return await self.adapter.check_availability()
        except Exception:
            return False