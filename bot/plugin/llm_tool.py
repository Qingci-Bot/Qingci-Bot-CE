"""插件级 LLM 工具声明（Function Calling）

声明机制（`LlmToolSpec` / `llm_tool` / `begin_tool_collection` /
`end_tool_collection`）统一由独立插件 SDK 维护（`qingci_plugin_sdk.llm_tool`），
本模块仅转发——避免双实现 / 双收集栈：插件无论从 `qingci_plugin_sdk` 还是
`bot.plugin.llm_tool` 导入装饰器，工具都写入**同一个** SDK 收集栈，
PluginManager 读取后经本模块的 `register_tools` 注册进 CE ToolRegistry。
（此前 CE 逐字复制了一套实现，SDK 路径导入的工具进 SDK 收集栈、CE 收集栈
为空，导致工具被静默丢弃。）

用法（模块级装饰器）：
    from bot.plugin.llm_tool import llm_tool

    @llm_tool(description="查询城市天气")
    def get_weather(city: str = "北京") -> str:
        return f"{city}: 晴 25°C"

工具注册名自动带插件名前缀（<plugin_name>_<tool_name>），避免跨插件冲突。
"""

from qingci_plugin_sdk.llm_tool import (
    LlmToolSpec,
    begin_tool_collection,
    end_tool_collection,
    llm_tool,
)

from ..llm.tools import ToolRegistry

__all__ = [
    "LlmToolSpec",
    "llm_tool",
    "begin_tool_collection",
    "end_tool_collection",
    "register_tools",
]


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
