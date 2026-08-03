"""LiteLLM 统一适配器

基于 litellm.acompletion 实现，支持 100+ LLM 提供商：
- OpenAI / DeepSeek / Ollama / Claude / Gemini 等
- 自定义 OpenAI 兼容 API（provider=custom）

provider 映射规则：
- openai  -> model 原样传入
- deepseek/ollama/<other> -> f"{provider}/{model}"
- custom  -> f"openai/{model}" + api_base（兼容任意 OpenAI 协议服务）
"""

import logging
from typing import Any, AsyncIterator, Optional

import litellm

from .adapter import LLMAdapter

logger = logging.getLogger("qingci-bot.llm.litellm_adapter")

# 关闭 litellm 的冗余日志与遥测
litellm.suppress_debug_info = True
try:
    litellm.set_verbose(False)
except (AttributeError, TypeError):
    pass  # 某些 litellm 版本中 set_verbose 不是可调用对象


class LiteLLMAdapter(LLMAdapter):
    """基于 litellm 的统一 LLM 适配器"""

    def __init__(
        self,
        provider: str = "openai",
        api_url: str = "",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
    ):
        self._provider = provider
        self._api_url = api_url.rstrip("/") if api_url else ""
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    # ============ 内部构造 ============

    def _build_model(self) -> str:
        """构造 litellm 的 model 字符串

        路由规则：
        - model 已带 provider 前缀（如 "deepseek/deepseek-chat"）-> 原样使用
        - provider=custom 或 api_url 非空 -> "openai/{model}"（走 OpenAI 兼容协议 + api_base）
          这样兼容旧 OpenAIAdapter 行为：自定义 endpoint 统一按 OpenAI 协议调用
        - 其他 provider（deepseek/ollama 等）且无 api_url -> "{provider}/{model}"（litellm 直连官方）
        """
        model = self._model
        if "/" in model:
            return model
        provider = self._provider
        # custom 或显式 api_url：统一走 OpenAI 兼容协议
        if provider == "custom" or self._api_url:
            return f"openai/{model}"
        if provider in ("openai", ""):
            return model
        return f"{provider}/{model}"

    def _build_kwargs(
        self,
        messages: list[dict],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: float,
        tools: Optional[list[dict]] = None,
        images: Optional[list[str]] = None,
        stream: bool = False,
        **extra,
    ) -> dict[str, Any]:
        full_messages: list[dict] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        # 处理多模态：将最后一条 user 文本消息转为 content 数组
        if images and full_messages:
            last = full_messages[-1]
            if last.get("role") == "user" and isinstance(last.get("content"), str):
                full_messages[-1] = self.build_image_message(last["content"], images)
            elif last.get("role") != "user" or not isinstance(last.get("content"), str):
                logger.warning("images 参数被忽略：最后一条消息不是 user 文本消息")

        kwargs: dict[str, Any] = {
            "model": self._build_model(),
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            "timeout": self._timeout,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        # api_url 非空时作为自定义 endpoint（兼容旧 OpenAIAdapter 行为）
        if self._api_url:
            kwargs["api_base"] = self._api_url
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        kwargs.update(extra)
        return kwargs

    # ============ 接口实现 ============

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
        try:
            response = await litellm.acompletion(
                **self._build_kwargs(
                    messages,
                    system_prompt,
                    max_tokens,
                    temperature,
                    tools=tools,
                    images=images,
                    stream=False,
                    **kwargs,
                )
            )
        except Exception as e:
            logger.error(f"litellm 调用失败: {e}")
            raise

        choices = response.choices or []
        if not choices:
            return ""
        message = choices[0].message
        # 若模型返回 tool_calls，记录警告但不混入文本历史
        if getattr(message, "tool_calls", None):
            logger.warning("模型返回了 tool_calls，当前版本不支持 Function Calling 循环，已忽略")
            return getattr(message, "content", "") or ""
        return getattr(message, "content", "") or ""

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[str]:
        try:
            response = await litellm.acompletion(
                **self._build_kwargs(
                    messages,
                    system_prompt,
                    max_tokens,
                    temperature,
                    stream=True,
                    **kwargs,
                )
            )
        except Exception as e:
            logger.error(f"litellm 流式调用失败: {e}")
            raise
        async for chunk in response:
            try:
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
            except (IndexError, AttributeError):
                continue

    async def check_availability(self) -> bool:
        try:
            await litellm.acompletion(
                **self._build_kwargs(
                    [{"role": "user", "content": "ping"}],
                    system_prompt="回复 pong",
                    max_tokens=10,
                    stream=False,
                )
            )
            return True
        except Exception as e:
            logger.warning(f"LLM 可用性检查失败: {e}")
            return False

    async def close(self):
        """关闭适配器资源

        注意：litellm 内部使用模块级单例 httpx 客户端，由多个适配器实例共享。
        不手动关闭以避免影响其他实例。连接池由 litellm 自行管理。
        """
        pass
