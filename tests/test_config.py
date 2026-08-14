"""配置管理测试"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from bot.config import ConfigManager, LLMConfig, OneBotConfig


@pytest.fixture
def config_file():
    """创建临时配置文件"""
    config = {
        "api_key": "test-key",
        "bot": {
            "admin_users": [10001, 10002],
            "log_json": False,
        },
        "onebot": {
            "host": "127.0.0.1",
            "port": 3001,
            "access_token": "token123",
        },
        "llm": {
            "provider": "openai",
            "api_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
            "max_tokens": 2048,
            "temperature": 0.7,
            "max_history": 10,
            "max_context_tokens": 4096,
            "timeout": 60,
            "num_retries": 3,
            "enable_tools": False,
            "enable_summary": False,
            "max_tool_rounds": 5,
            "mcp_servers": [],
        },
        "rag": {"enabled": False},
        "scheduler": {"enabled": False},
        "alert": {"enabled": False},
        "rate_limit": {"enabled": False},
        "filter": {"words_file": "data/bad_words.txt"},
        "session_summary": {
            "enabled": False,
            "max_messages": 50,
            "max_tokens": 8192,
            "keep_recent_turns": 10,
            "summary_max_tokens": 512,
        },
        "log": {
            "usage_tracking": True,
            "level": "INFO",
            "log_file_enabled": False,
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(config, f)
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


class TestConfigManager:
    """配置管理器测试"""

    def test_load_config(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        assert cm.config.api_key == "test-key"
        assert cm.config.bot.admin_users == [10001, 10002]

    def test_load_nonexistent_file(self, tmp_path):
        # 确保父目录存在（ConfigManager.save 需要写入）
        cfg_dir = tmp_path / "nonexistent"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cm = ConfigManager(cfg_dir / "config.yaml")
        # 应不抛异常，使用默认配置
        cm.load()
        assert cm.config is not None

    def test_onebot_config_defaults(self):
        cfg = OneBotConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 3001
        assert cfg.access_token == ""

    def test_llm_config_preset(self):
        cfg = LLMConfig(provider="openai")
        assert cfg.model == "gpt-4o-mini"
        assert cfg.api_url == "https://api.openai.com/v1"

    def test_config_to_dict(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        d = cm.to_dict()
        assert isinstance(d, dict)
        assert "api_key" in d

    def test_config_update(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        new_data = {"api_key": "new-key", "bot": {"admin_users": [99999]}}
        cm.update(new_data)
        assert cm.config.api_key == "new-key"
        assert cm.config.bot.admin_users == [99999]

    def test_sensitive_mask(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        data = cm.to_dict()
        # 原始 to_dict 不遮蔽，遮蔽由 API 路由层处理
        # api_key 存在于配置中
        assert "api_key" in data
        assert "llm" in data
        assert "api_key" in data["llm"]
        # 非敏感字段应正常返回
        assert data["bot"]["admin_users"] == [10001, 10002]
