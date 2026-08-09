"""插件系统测试公共 fixture

提供轻量 FakeBot，模拟 PluginManager._init_plugin 所需的最小依赖，
避免启动真实 Bot（数据库 / LLM / OneBot 连接）。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

# 使 tests/plugin_pkg 下的测试插件模块可被 importlib 加载
TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))

import pytest

from bot.plugin.manager import PluginManager


class FakeConfig:
    """模拟 ConfigManager 的属性访问（仅提供插件所需字段）"""
    def __init__(self):
        self.bot = SimpleNamespace(admin_users=[10001])
        self.rate_limit = SimpleNamespace(enabled=False)


class FakeBot:
    """最小化 Bot 实例，可被 PluginManager._init_plugin 注入"""

    def __init__(self):
        self.config = FakeConfig()
        self.plugin_manager = PluginManager()
        # 插件依赖注入所需属性（测试插件不使用真实实现）
        self.db = None
        self.connection = None
        self.llm = None
        self.scheduler = None
        self.tool_registry = None
        self.knowledge_store = None
        self.sensitive_filter = None


@pytest.fixture
def bot():
    """提供全新 FakeBot（每个测试独立实例）"""
    return FakeBot()
