"""测试插件：注册插件级 Web API（供 test_plugin_webapi.py 使用）"""

from fastapi.responses import JSONResponse

from bot.plugin.base import PluginBase


class WebApiPlugin(PluginBase):
    name = "webapi"
    version = "1.0.0"
    author = "test"
    description = "Web API 测试插件"

    async def on_load(self):
        self.register_api("ping", self._api_ping, methods=["GET"], description="ping")
        self.register_api("echo", self._api_echo, methods=["POST"])
        self.register_api("boom", self._api_boom, methods=["GET"])
        self.register_api("raw", self._api_raw, methods=["GET"])

    async def on_unload(self):
        pass

    async def _api_ping(self, request):
        # inst_id 暴露实例身份：验证路由热重载后动态解析到新实例
        return {"pong": True, "plugin": self.name, "inst_id": id(self)}

    async def _api_echo(self, request):
        payload = await request.json()
        return {"echo": payload}, 201

    async def _api_boom(self, request):
        raise RuntimeError("boom")

    async def _api_raw(self, request):
        return JSONResponse({"raw": 1})
