"""插件测试工具 — TestBot 轻量测试环境

提供不启动真实 Bot（无数据库 / LLM / OneBot 连接）的测试沙箱，
让插件作者用 pytest 模拟消息事件并断言回复。

用法（配合 pytest-asyncio）：
    import pytest
    from bot.testing import TestBot, private_message

    @pytest.fixture
    def bot():
        return TestBot()

    async def test_ping(bot):
        await bot.load_plugin("my_plugin")   # 模块路径（须已加入 sys.path）
        reply = await bot.send(private_message("/ping"))
        assert reply == "pong"

    async def test_sent_messages(bot):
        await bot.load_plugin("my_plugin")
        await bot.send(private_message("指令"))
        assert bot.sent_messages[0] == ("private", 10001, "回复内容")
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from ..core.di import DIContainer
from ..core.dispatcher import MessageDispatcher
from ..core.event_bus import EventBus
from ..core.session_state import SessionStateManager
from ..llm.tools import ToolRegistry
from ..plugin.base import PluginStatus
from ..plugin.manager import PluginManager

if TYPE_CHECKING:
    from ..core.bot import QingciBot

logger = logging.getLogger("qingci-bot.testing")

# 测试默认 Bot QQ 号
DEFAULT_SELF_ID = 20002


class FakeConfig:
    """最小配置，供权限/插件级配置检查使用"""

    def __init__(self):
        self.bot = SimpleNamespace(
            admin_users=[10001],  # 默认 10001 视为普通管理员，方便测试权限
            super_admin=10001,  # 默认 10001 同时为超级管理员，方便测试权限
            super_users=[],
        )
        self.rate_limit = SimpleNamespace(enabled=False)
        self.llm = SimpleNamespace(enabled=False, api_key="", api_url="")
        self.rag = SimpleNamespace(enabled=False)
        self.plugins_config: dict[str, dict] = {}

    def get_plugin_config(self, plugin_name: str) -> dict | None:
        """获取插件级配置（与 ConfigManager.get_plugin_config 一致）"""
        return self.plugins_config.get(plugin_name)


class FakeConnection:
    """记录 API 调用与发送消息的假连接

    - send_* 方法记录到 connection.sent
    - call_api 记录到 connection.api_calls 并返回空 dict
    - 插件内部主动发消息可在此断言
    """

    def __init__(self):
        self.sent: list[tuple[str, int, str]] = []
        # [(message_type, target_id, message), ...]
        self.api_calls: list[tuple[str, dict]] = []
        self._api_call_hooks: list[Any] = []

    def on_api_call(self, handler) -> None:
        """注册平台接口调用钩子（与 OneBotConnection.on_api_call 对齐）"""
        if handler not in self._api_call_hooks:
            self._api_call_hooks.append(handler)

    async def call_api(self, action: str, params: dict | None = None, timeout: float = 30) -> dict:
        params = params or {}
        for hook in list(self._api_call_hooks):
            modified = hook(action, dict(params))
            if hasattr(modified, "__await__"):
                modified = await modified
            if modified is not None:
                params = modified
        self.api_calls.append((action, params or {}))
        return {}

    async def send_private_msg(self, user_id: int, message: str) -> dict:
        self.sent.append(("private", user_id, message))
        return {"message_id": f"sent-{len(self.sent)}"}

    async def send_group_msg(self, group_id: int, message: str) -> dict:
        self.sent.append(("group", group_id, message))
        return {"message_id": f"sent-{len(self.sent)}"}

    async def send_msg(self, message_type: str, target_id: int, message: str) -> dict:
        if message_type == "private":
            return await self.send_private_msg(target_id, message)
        if message_type == "group":
            return await self.send_group_msg(target_id, message)
        raise ValueError(f"未知的 message_type: {message_type}")


class TestBot:
    """轻量测试 Bot：可加载插件、模拟事件、断言回复

    不启动真实网络/数据库/LLM。核心组件（Dispatcher / PluginManager /
    SessionStateManager / DIContainer）均为真实实现，保证测试与生产
    行为一致；连接与配置为最小 Fake 实现。
    """

    def __init__(self, config: Any | None = None, self_id: int = DEFAULT_SELF_ID):
        self.self_id = self_id
        self.config = config or FakeConfig()

        self.plugin_manager = PluginManager()
        self.dispatcher = MessageDispatcher()
        self.session_state = SessionStateManager()
        self.connection = FakeConnection()
        self.event_bus = EventBus()
        self.tool_registry = ToolRegistry()

        # DI 容器：与真实 Bot 一致注册核心服务
        self.di = DIContainer()
        self.di.register_sync(PluginManager, self.plugin_manager)
        self.di.register_sync(MessageDispatcher, self.dispatcher)
        self.di.register_sync(SessionStateManager, self.session_state)
        self.di.register_sync(EventBus, self.event_bus)
        self.di.register_sync(DIContainer, self.di)
        self.di.register_sync(TestBot, self)

        # 插件可能引用但测试不使用的服务
        self.db = None
        self.llm = None
        self.scheduler = None
        self.knowledge_store = None
        self.sensitive_filter = None

        # Matcher 运行前钩子（run_preprocessor），与真实 Bot 对齐
        self._matcher_preprocessors: list[Any] = []

        self._running = True  # 模拟已启动，事件可被处理

    # ---- 钩子注册 ----

    def add_matcher_preprocessor(self, fn) -> None:
        """注册 Matcher 运行前钩子（与 QingciBot.add_matcher_preprocessor 对齐）"""
        if fn not in self._matcher_preprocessors:
            self._matcher_preprocessors.append(fn)

    def register_api_hook(self, fn) -> None:
        """注册平台接口调用钩子（转发到 connection.on_api_call）"""
        self.connection.on_api_call(fn)

    # ---- 插件加载 ----

    async def load_plugin(self, module_path: str) -> bool:
        """加载插件模块（须可 import）

        Args:
            module_path: 模块路径，如 "my_plugin" 或 "tests.plugins.my_plugin"
        """
        return await self.plugin_manager.load_external(module_path, self)

    def add_plugin_dir(self, directory: str) -> None:
        """将插件目录加入 sys.path，便于 load_plugin 使用相对模块名"""
        p = Path(directory)
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    # ---- 事件发送 ----

    async def send(self, event: dict) -> str | None:
        """发送事件并返回 Bot 的回复（str），无回复返回 None

        完整走 Dispatcher 调度链路：dispatch → run_matchers →
        未命中时回退插件 on_message 回调。
        """
        ctx = self.dispatcher.dispatch(event)
        post_type = ctx.post_type or event.get("post_type", "")

        if post_type != "message":
            reply, blocked = await self.dispatcher._run_event_matchers(
                cast("QingciBot", self), event, ctx
            )
            if reply is not None or blocked:
                return reply
            # 旧式回调
            for plugin in list(self.plugin_manager.plugins.values()):
                if plugin.status != PluginStatus.LOADED:
                    continue
                if post_type == "notice":
                    await plugin.on_notice(event)
                elif post_type == "request":
                    await plugin.on_request(event)
            return None

        reply, blocked = await self.dispatcher.run_matchers(cast("QingciBot", self), event, ctx)
        if reply is not None:
            return reply
        if blocked:
            return None

        # Matcher 未命中时回退旧式 on_message
        for plugin in list(self.plugin_manager.plugins.values()):
            if plugin.status != PluginStatus.LOADED:
                continue
            if self._plugin_has_event_matcher(plugin, "message"):
                continue
            try:
                reply = await plugin.on_message(ctx)
                if reply:
                    return reply
            except Exception:
                logger.exception(f"插件 {plugin.name} on_message 异常")
        return None

    async def send_private(
        self, text: str, user_id: int = 10001, *, at_bot: bool = False
    ) -> str | None:
        """发送私聊消息"""
        from .events import private_message

        return await self.send(
            private_message(text, user_id=user_id, self_id=self.self_id, at_bot=at_bot)
        )

    async def send_group(
        self, text: str, user_id: int = 10001, group_id: int = 20001, *, at_bot: bool = False
    ) -> str | None:
        """发送群聊消息"""
        from .events import group_message

        return await self.send(
            group_message(
                text, user_id=user_id, group_id=group_id, self_id=self.self_id, at_bot=at_bot
            )
        )

    # ---- 断言辅助 ----

    @property
    def sent_messages(self) -> list[tuple[str, int, str]]:
        """插件通过 connection 主动发送的所有消息 (type, target, text)"""
        return list(self.connection.sent)

    @property
    def api_calls(self) -> list[tuple[str, dict]]:
        """插件调用过的所有 OneBot API (action, params)"""
        return list(self.connection.api_calls)

    def get_plugin(self, name: str):
        """获取已加载的插件实例"""
        return self.plugin_manager.get(name)

    # ---- 内部 ----

    @staticmethod
    def _plugin_has_event_matcher(plugin, post_type: str) -> bool:
        """插件是否注册了指定事件类型的 Matcher"""
        for m in getattr(plugin, "matchers", None) or []:
            if getattr(m, "event_type", "message") == post_type:
                return True
        return False

    async def cleanup(self) -> None:
        """卸载所有插件并清空会话状态"""
        await self.plugin_manager.shutdown()
        await self.session_state.clear_all()
