"""插件系统测试公共 fixture

复用 bot.testing.TestBot 作为轻量测试环境（无需真实数据库 / LLM / OneBot 连接），
保证测试与生产调度行为一致。
"""

import sys
from pathlib import Path

# 使 tests/plugin_pkg 下的测试插件模块可被 importlib 加载
TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))

import pytest  # noqa: E402

from bot.testing import TestBot  # noqa: E402


@pytest.fixture
def bot():
    """提供全新 TestBot（每个测试独立实例）"""
    return TestBot()
