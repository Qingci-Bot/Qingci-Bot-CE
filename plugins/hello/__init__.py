"""Hello World 示例插件（演示 SDK 新能力）

使用方式:
    cd Plugins-SDK
    uv pip install -e .
    python -c "from qingci_plugin_sdk import PluginBase; print('SDK OK')"

开发完成后，将 hello/ 目录复制到 Qingci-Bot/plugins/ 即可加载。

本示例演示 SDK 新增能力：
- i18n 国际化（self._ = self.i18n.t）
- 全局生命周期钩子（on_startup / on_shutdown / on_bot_connect / on_metaevent）
- 指令增强：别名 + 子指令 + 类型化参数（args_schema）
- LLM 工具声明（@llm_tool）
- 插件数据目录（self.data_dir）
"""

import logging

from qingci_plugin_sdk import (
    MatcherContext,
    PluginBase,
    llm_tool,
    on_command,
)

logger = logging.getLogger("qingci-bot.plugin.hello")


class HelloPlugin(PluginBase):
    name = "hello"
    version = "1.4.0"
    author = "Qingci-Bot"
    description = "Hello World 示例插件（演示 SDK 新能力）"

    async def on_load(self):
        # 命令：别名 hello / hi / 你好
        self.matchers.append(
            on_command(
                "hello",
                aliases=("hi", "你好"),
                description="打个招呼",
            )(self._handle_hello)
        )

        # 子指令：/greet zh 或 /greet en
        async def _greet_zh(ctx: MatcherContext) -> str:
            return "你好呀！"

        async def _greet_en(ctx: MatcherContext) -> str:
            return "Hello, friend!"

        self.matchers.append(
            on_command(
                "greet",
                description="多语言问候",
                subcommands={"zh": _greet_zh, "en": _greet_en},
            )(self._handle_greet)
        )

        # 类型化参数：/weather Beijing 3
        self.matchers.append(
            on_command(
                "weather",
                description="天气预报（类型化参数示例）",
                args_schema={"city": str, "days": int},
            )(self._handle_weather)
        )

    async def _handle_hello(self, ctx: MatcherContext) -> str:
        name = ctx.args.strip() or "world"
        return f"Hello, {name}!"

    async def _handle_greet(self, ctx: MatcherContext) -> str:
        return "子指令: /greet zh 或 /greet en"

    async def _handle_weather(self, ctx: MatcherContext, city: str = "", days: int = 1):
        return f"{city}: 未来 {days} 天预报略"

    async def on_unload(self):
        logger.info(f"[{self.name}] 插件已卸载")

    # ---- 全局生命周期钩子（可选覆写） ----

    async def on_startup(self):
        logger.info(f"[{self.name}] 启动完成，数据目录: {self.data_dir}")

    async def on_shutdown(self):
        logger.info(f"[{self.name}] 关闭")

    async def on_bot_connect(self):
        logger.info(f"[{self.name}] QQ 会话已连接")

    async def on_metaevent(self, event: dict):
        return None

    # ---- 模块级 LLM 工具（装饰器注册，PluginManager 自动收集） ----

    @llm_tool(name="get_time", description="获取当前北京时间")
    async def get_time(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


# 模块级 LLM 工具示例：独立函数也可用 @llm_tool 声明
@llm_tool(name="echo", description="原样返回输入")
async def echo(text: str) -> str:
    return text
