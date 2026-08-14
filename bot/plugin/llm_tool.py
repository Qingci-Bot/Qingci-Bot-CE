"""插件级 LLM 工具声明（Function Calling）

允许插件用装饰器直接注册 Function Calling 工具，让插件参与 LLM 推理，
构建「LLM 原生插件」生态。工具在插件加载时注册到全局 ToolRegistry，
卸载时自动注销。

用法（模块级装饰器）：
    from bot.plugin.llm_tool import llm_tool

    @llm_tool(description="查询城市天气")
    def get_weather(city: str = "北京") -> str:
        return f"{city}: 晴 25°C"

工具注册名自动带插件名前缀（<plugin_name>_<tool_name>），避免跨插件冲突。

也可以显式声明标准 JSON Schema 参数：
    @llm_tool(
        name="sum",
        description="计算两个整数之和",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "加数"},
                "b": {"type": "integer", "description": "加数"},
            },
            "required": ["a", "b"],
        },
    )
    def add(a: int, b: int) -> int:
        return a + b
"""

import threading
from dataclasses import dataclass, field
from typing import Any

from ..llm.tools import ToolRegistry


@dataclass
class LlmToolSpec:
    """LLM 工具声明（插件加载时收集，注册到全局注册表）"""

    name: str
    handler: Any
    description: str = ""
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


# 模块级工具收集栈：插件加载时设置，收集到的工具关联到当前插件
_tool_collector: list[LlmToolSpec] | None = None
_tool_lock = threading.Lock()


def llm_tool(
    name: str | None = None,
    description: str = "",
    parameters: dict | None = None,
) -> Any:
    """声明插件级 LLM 工具（装饰器工厂）

    Args:
        name: 工具名（默认取函数名）
        description: 工具描述（供模型判断何时调用）
        parameters: 标准 JSON Schema 参数定义（缺省时为空对象）
    """

    def decorator(func: Any):
        spec = LlmToolSpec(
            name=name or func.__name__,
            handler=func,
            description=description or (func.__doc__ or "").strip(),
            parameters=parameters or {"type": "object", "properties": {}},
        )
        with _tool_lock:
            if _tool_collector is not None:
                _tool_collector.append(spec)
        return func

    return decorator


def begin_tool_collection() -> list[LlmToolSpec]:
    """开始收集模块级 LLM 工具，返回收集列表"""
    global _tool_collector
    with _tool_lock:
        _tool_collector = []
        return _tool_collector


def end_tool_collection():
    """结束收集"""
    global _tool_collector
    with _tool_lock:
        _tool_collector = None


def register_tools(
    registry: ToolRegistry,
    plugin_name: str,
    specs: list[LlmToolSpec],
) -> list[str]:
    """将插件声明的工具注册到全局注册表

    工具名自动加插件前缀（<plugin_name>_<name>），避免跨插件冲突。
    返回注册成功的工具名列表（真实注册名）。
    """
    registered: list[str] = []
    for spec in specs:
        full_name = f"{plugin_name}_{spec.name}"
        try:
            registry.register(
                name=full_name,
                description=spec.description,
                parameters=spec.parameters,
                handler=spec.handler,
            )
            registered.append(full_name)
        except ValueError:
            import logging

            logging.getLogger("qingci-bot.plugin.llm_tool").warning(
                f"插件 {plugin_name} 工具注册失败（可能重名）: {full_name}"
            )
    return registered
