"""基于独立插件 SDK（qingci_plugin_sdk）的测试插件

验证宿主 bot 能识别并加载 SDK 式外部插件，且 data_dir 被重定向到
bot 的可写数据根（实例隔离）。
"""

from qingci_plugin_sdk.base import PluginBase
from qingci_plugin_sdk.matcher import MatcherContext, on_command


class SdkPlugin(PluginBase):
    name = "sdk_plugin"
    version = "1.0.0"
    description = "SDK 式测试插件"

    async def on_load(self):
        self.matchers.append(on_command("sdkping")(self._ping))

    async def on_unload(self):
        pass

    async def _ping(self, ctx: MatcherContext) -> str:
        return "sdk pong"
