"""OpenAI 兼容 API 适配器 - 支持 OpenAI / DeepSeek / Ollama 等"""

import json
import logging
from typing import AsyncIterator, Optional

import httpx

from .adapter import LLMAdapter

logger = logging.getLogger("qingci-bot.llm.openai")


class OpenAIAdapter(LLMAdapter):
    """OpenAI 兼容 API 适配器"""

    def __init__(
        self,
        api_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client: Optional[httpx.AsyncClient] = None
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return "openai"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        client = await self._get_client()
        response = await client.post(
            f"{self.api_url}/chat/completions",
            json={
                "model": self.model,
                "messages": full_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                **kwargs,
            },
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "")

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[str]:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        client = await self._get_client()
        async with client.stream(
            "POST",
            f"{self.api_url}/chat/completions",
            json={
                "model": self.model,
                "messages": full_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
                **kwargs,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def check_availability(self) -> bool:
        try:
            await self.chat(
                [{"role": "user", "content": "ping"}],
                system_prompt="回复 pong",
                max_tokens=10,
            )
            return True
        except Exception:
            return False