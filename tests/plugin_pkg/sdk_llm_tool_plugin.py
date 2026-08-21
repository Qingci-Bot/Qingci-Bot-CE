"""on_load 场景用 SDK 路径导入 llm_tool 的插件（对应官方 hello 用法）"""

from qingci_plugin_sdk import llm_tool

from bot.plugin.base import PluginBase


@llm_tool(description="获取当前时间")
def sdk_get_time() -> str:
    return "12:00"


class SdkLlmToolPlugin(PluginBase):
    name = "sdk_llm_tool"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass
