"""LiteLLM 适配器测试

通过 mock 模块级 _get_litellm（延迟导入入口）验证，无需真实网络：
- _build_model provider 路由（openai / custom / api_url / 带前缀模型）
- _build_kwargs（system_prompt 前置、多模态 images、tools、api_key/api_base）
- chat_detail / chat / chat_stream（正常、空 choices、异常传播）
- check_availability（成功、鉴权/超时/网络/普通异常分类）
- _get_litellm 延迟导入与日志静默
"""

import sys
import types
from types import SimpleNamespace

import pytest

from bot.llm.litellm_adapter import LiteLLMAdapter, _get_litellm


def _fake_litellm():
    """构造带 acompletion 与异常类的假 litellm 模块"""
    mod = types.ModuleType("litellm")
    mod.AuthenticationError = type("AuthenticationError", (Exception,), {})
    mod.Timeout = type("Timeout", (Exception,), {})
    mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    mod.acompletion = None  # 测试中替换
    return mod


@pytest.fixture
def fake_lm(monkeypatch):
    """把 _get_litellm 替换为返回假 litellm 模块"""
    mod = _fake_litellm()
    monkeypatch.setattr("bot.llm.litellm_adapter._get_litellm", lambda: mod)
    return mod


def _resp(content: str = "", tool_calls=None, usage=None):
    return SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
    )


# ---------- provider 路由 ----------


def test_build_model_keeps_prefixed():
    a = LiteLLMAdapter(provider="deepseek", model="deepseek/deepseek-chat")
    assert a._build_model() == "deepseek/deepseek-chat"


def test_build_model_custom_prefix():
    a = LiteLLMAdapter(provider="custom", model="my-model")
    assert a._build_model() == "openai/my-model"


def test_build_model_api_url_prefix():
    a = LiteLLMAdapter(provider="ollama", api_url="http://localhost:11434/v1", model="llama3")
    assert a._build_model() == "openai/llama3"


def test_build_model_openai_plain():
    assert LiteLLMAdapter(provider="openai", model="gpt-4o-mini")._build_model() == "gpt-4o-mini"
    assert LiteLLMAdapter(provider="", model="gpt-4o-mini")._build_model() == "gpt-4o-mini"


def test_build_model_other_provider_prefix():
    assert (
        LiteLLMAdapter(provider="deepseek", model="deepseek-chat")._build_model()
        == "deepseek/deepseek-chat"
    )


# ---------- kwargs 构造 ----------


def test_build_kwargs_basic():
    a = LiteLLMAdapter(provider="openai", api_key="k", model="gpt-4o-mini")
    kw = a._build_kwargs(
        [{"role": "user", "content": "hi"}], system_prompt="sys", max_tokens=100, temperature=0.5
    )
    assert kw["model"] == "gpt-4o-mini"
    assert kw["messages"][0] == {"role": "system", "content": "sys"}
    assert kw["messages"][1] == {"role": "user", "content": "hi"}
    assert kw["max_tokens"] == 100
    assert kw["temperature"] == 0.5
    assert kw["stream"] is False
    assert kw["timeout"] == a._timeout
    assert kw["num_retries"] == a._num_retries
    assert kw["api_key"] == "k"
    assert "api_base" not in kw


def test_build_kwargs_api_base():
    a = LiteLLMAdapter(provider="custom", api_url="https://api.example.com/v1", api_key="k")
    kw = a._build_kwargs([{"role": "user", "content": "hi"}], None, 10, 0.7)
    assert kw["api_base"] == "https://api.example.com/v1"


def test_build_kwargs_images():
    a = LiteLLMAdapter(provider="openai")
    kw = a._build_kwargs(
        [{"role": "user", "content": "看图"}], None, 10, 0.7, images=["http://x/img.png"]
    )
    last = kw["messages"][-1]
    assert last["role"] == "user"
    assert last["content"][0] == {"type": "text", "text": "看图"}
    assert last["content"][1] == {"type": "image_url", "image_url": {"url": "http://x/img.png"}}


def test_build_kwargs_images_ignored_non_user():
    a = LiteLLMAdapter(provider="openai")
    kw = a._build_kwargs(
        [{"role": "assistant", "content": "前一条"}], None, 10, 0.7, images=["http://x/img.png"]
    )
    assert kw["messages"][-1]["content"] == "前一条"  # 未转换


def test_build_kwargs_tools():
    a = LiteLLMAdapter(provider="openai")
    tools = [{"type": "function", "function": {"name": "f"}}]
    kw = a._build_kwargs([{"role": "user", "content": "hi"}], None, 10, 0.7, tools=tools)
    assert kw["tools"] == tools
    assert kw["tool_choice"] == "auto"


# ---------- chat_detail ----------


async def test_chat_detail_ok(fake_lm):
    async def acompletion(**kwargs):
        return _resp(
            content="你好",
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    fake_lm.acompletion = acompletion
    a = LiteLLMAdapter(provider="openai", api_key="k")
    r = await a.chat_detail([{"role": "user", "content": "hi"}])
    assert r.content == "你好"
    assert r.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert r.tool_calls is None


async def test_chat_detail_no_usage(fake_lm):
    async def acompletion(**kwargs):
        return _resp(content="hi")

    fake_lm.acompletion = acompletion
    r = await LiteLLMAdapter().chat_detail([{"role": "user", "content": "hi"}])
    assert r.content == "hi"
    assert r.usage is None


async def test_chat_detail_empty_choices(fake_lm):
    async def acompletion(**kwargs):
        return SimpleNamespace(usage=None, choices=[])

    fake_lm.acompletion = acompletion
    r = await LiteLLMAdapter().chat_detail([{"role": "user", "content": "hi"}])
    assert r.content == ""


async def test_chat_detail_tool_calls(fake_lm):
    tc = SimpleNamespace(id="1", function=SimpleNamespace(name="f", arguments="{}"))

    async def acompletion(**kwargs):
        return _resp(content="", tool_calls=[tc])

    fake_lm.acompletion = acompletion
    r = await LiteLLMAdapter().chat_detail([{"role": "user", "content": "hi"}])
    assert r.tool_calls == [tc]


async def test_chat_detail_propagates_error(fake_lm):
    async def acompletion(**kwargs):
        raise RuntimeError("boom")

    fake_lm.acompletion = acompletion
    with pytest.raises(RuntimeError):
        await LiteLLMAdapter().chat_detail([{"role": "user", "content": "hi"}])


# ---------- chat / chat_stream ----------


async def test_chat_wraps_detail(fake_lm):
    async def acompletion(**kwargs):
        return _resp(content="ok")

    fake_lm.acompletion = acompletion
    assert await LiteLLMAdapter().chat([{"role": "user", "content": "hi"}]) == "ok"


async def test_chat_stream_chunks(fake_lm):
    async def acompletion(**kwargs):
        assert kwargs["stream"] is True

        async def _agen():
            for c in ["你", "好", "！"]:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c))])

        return _agen()

    fake_lm.acompletion = acompletion
    got = [c async for c in LiteLLMAdapter().chat_stream([{"role": "user", "content": "hi"}])]
    assert "".join(got) == "你好！"


async def test_chat_stream_skips_empty_chunks(fake_lm):
    async def acompletion(**kwargs):
        async def _agen():
            yield SimpleNamespace(choices=[])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))])

        return _agen()

    fake_lm.acompletion = acompletion
    got = [c async for c in LiteLLMAdapter().chat_stream([{"role": "user", "content": "hi"}])]
    assert got == ["a"]


async def test_chat_stream_raises(fake_lm):
    async def acompletion(**kwargs):
        raise RuntimeError("boom")

    fake_lm.acompletion = acompletion
    with pytest.raises(RuntimeError):
        async for _ in LiteLLMAdapter().chat_stream([{"role": "user", "content": "hi"}]):
            pass


# ---------- check_availability ----------


async def test_check_availability_ok(fake_lm):
    async def acompletion(**kwargs):
        return _resp(content="pong")

    fake_lm.acompletion = acompletion
    a = LiteLLMAdapter()
    assert await a.check_availability() is True
    assert a.last_error == ""


@pytest.mark.parametrize(
    "exc_cls",
    ["AuthenticationError", "Timeout", "APIConnectionError"],
)
async def test_check_availability_error_classes(fake_lm, exc_cls):
    exc = getattr(fake_lm, exc_cls)

    async def acompletion(**kwargs):
        raise exc("boom")

    fake_lm.acompletion = acompletion
    a = LiteLLMAdapter()
    assert await a.check_availability() is False
    assert exc_cls in a.last_error


async def test_check_availability_plain_error(fake_lm):
    async def acompletion(**kwargs):
        raise ValueError("bad")

    fake_lm.acompletion = acompletion
    a = LiteLLMAdapter()
    assert await a.check_availability() is False
    assert "ValueError" in a.last_error


# ---------- 延迟导入 ----------


def test_get_litellm_lazy_import(monkeypatch):
    fake = types.ModuleType("litellm")
    fake.suppress_debug_info = False
    fake.set_verbose = lambda v: None
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr("bot.llm.litellm_adapter._litellm", None)
    lm = _get_litellm()
    assert lm is fake
    assert fake.suppress_debug_info is True


def test_get_litellm_without_set_verbose(monkeypatch):
    fake = types.ModuleType("litellm")
    fake.suppress_debug_info = False
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr("bot.llm.litellm_adapter._litellm", None)
    assert _get_litellm() is fake  # set_verbose 缺失静默通过
