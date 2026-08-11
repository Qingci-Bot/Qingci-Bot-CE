"""Qingci-Bot CE 最小示例插件

将此文件复制到 plugins/ 目录即可自动加载。
发送 /hello 或 "你好" 测试。
"""

import logging

from bot.plugin.base import PluginBase
from bot.plugin.matcher import MatcherContext, on_command, on_keyword

logger = logging.getLogger("qingci-bot.plugin.hello")


class HelloPlugin(PluginBase):
    """最小插件示例 —— 适合快速上手"""

    name = "hello"
    version = "1.0.0"
    author = "Qingci-Bot CE"
    description = "一个简单的问候插件"

    async def on_load(self):
        logger.info("Hello 插件已加载")

        # 命令触发：/hello
        self.matchers.append(
            on_command(
                "hello",
                description="打个招呼",
                priority=10,
            )(self._hello)
        )

        # 关键词触发：消息含"你好"
        self.matchers.append(
            on_keyword(
                "你好",
                description="响应问候",
                priority=10,
            )(self._greet)
        )

    async def on_unload(self):
        logger.info("Hello 插件已卸载")

    async def _hello(self, ctx: MatcherContext) -> str:
        name = ctx.args.strip() or "世界"
        return f"Hello, {name}!"

    async def _greet(self, ctx: MatcherContext) -> str:
        user_id = ctx.user_id or "陌生人"
        return f"你好呀！(来自 Hello 插件)"