"""LiteLLM 统一适配器

基于 litellm.acompletion 实现，支持 100+ LLM 提供商：
- OpenAI / DeepSeek / Ollama / Claude / Gemini 等
- 自定义 OpenAI 兼容 API（provider=custom）

provider 映射规则：
- openai  -> model 原样传入
- deepseek/ollama/<other> -> f"{provider}/{model}"
- custom  -> f"openai/{model}" + api_base（兼容任意 OpenAI 协议服务）
"""

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

from .adapter import ChatResult, LLMAdapter

logger = logging.getLogger("qingci-bot.llm.litellm_adapter")

# litellm 包导入耗时 ~3.5s（含依赖加载），顶层导入会拖慢 exe 启动。
# 延迟到首次真正调用 LLM 时才导入，仅在首条对话时付出一次性代价。
_litellm = None


def _get_litellm():
    """延迟导入 litellm 并完成一次性日志/遥测配置，返回模块引用"""
    global _litellm
    if _litellm is None:
        import litellm

        # 关闭 litellm 的冗余日志与遥测
        litellm.suppress_debug_info = True
        try:
            litellm.set_verbose(False)
        except (AttributeError, TypeError):
            pass  # 某些 litellm 版本中 set_verbose 不是可调用对象
        _litellm = litellm
    return _litellm


class LiteLLMAdapter(LLMAdapter):
    """基于 litellm 的统一 LLM 适配器"""

    def __init__(
        self,
        provider: str = "openai",
        api_url: str = "",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
        num_retries: int = 2,
    ):
        self._provider = provider
        self._api_url = api_url.rstrip("/") if api_url else ""
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._num_retries = num_retries
        self.last_error: str = ""  # 最近一次可用性检查失败的具体原因

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
            "num_retries": self._num_retries,
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
        try:
            litellm = _get_litellm()
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

        # 提取 token 用量（服务未提供时保持 None）
        usage: Optional[dict] = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage = {
                "prompt_tokens": getattr(raw_usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(raw_usage, "completion_tokens", 0) or 0,
            }

        choices = response.choices or []
        if not choices:
            return ChatResult(content="", usage=usage)
        message = choices[0].message
        # tool_calls 透传给调用方（Function Calling 循环在批次 3 使用）
        tool_calls = getattr(message, "tool_calls", None) or None
        return ChatResult(
            content=getattr(message, "content", "") or "",
            usage=usage,
            tool_calls=tool_calls,
        )

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
        # 包装 chat_detail：对外签名与异常行为保持不变，仅返回文本
        result = await self.chat_detail(
            messages,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            images=images,
            **kwargs,
        )
        return result.content

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[str]:
        try:
            litellm = _get_litellm()
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
                # choices 可能为 None 或空列表，先判空避免 TypeError/IndexError
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
            except (IndexError, AttributeError, TypeError):
                continue

    async def check_availability(self) -> bool:
        try:
            # 可用性探测使用较短超时，避免接口长时间阻塞
            litellm = _get_litellm()
            await litellm.acompletion(
                **self._build_kwargs(
                    [{"role": "user", "content": "ping"}],
                    system_prompt="回复 pong",
                    max_tokens=10,
                    temperature=0.7,
                    stream=False,
                    timeout=10,
                )
            )
            self.last_error = ""
            return True
        except Exception as e:
            err_type = type(e).__name__
            self.last_error = f"{err_type}: {e}"
            if isinstance(e, litellm.AuthenticationError):
                logger.warning(f"LLM 可用性检查失败（鉴权错误 {err_type}）: {e}")
            elif isinstance(e, (litellm.Timeout, asyncio.TimeoutError)):
                logger.warning(f"LLM 可用性检查超时（{err_type}）: {e}")
            elif isinstance(e, (litellm.APIConnectionError, OSError)):
                logger.warning(f"LLM 可用性检查失败（网络错误 {err_type}）: {e}")
            else:
                logger.warning(f"LLM 可用性检查失败（{err_type}）: {e}")
            return False

    async def close(self):
        """关闭适配器资源

        注意：litellm 内部使用模块级单例 httpx 客户端，由多个适配器实例共享。
        不手动关闭以避免影响其他实例。连接池由 litellm 自行管理。
        """
        pass
