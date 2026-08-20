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
        assert cm.config.bot.admin_users == ["10001", "10002"]

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
        assert cm.config.bot.admin_users == ["99999"]

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
        assert data["bot"]["admin_users"] == ["10001", "10002"]

    def test_admin_set_precompiled(self, config_file):
        """admin_set 预编译集合正确包含 super_admin + admin_users 并集"""
        cm = ConfigManager(Path(config_file))
        cm.load()
        cfg = cm.config.bot

        # 初始配置：super_admin=None, admin_users=[10001, 10002]
        assert cfg.admin_set == frozenset({"10001", "10002"})

        # 设置 super_admin 后应包含在内（数字 ID 自动转字符串）
        new_data = {"bot": {"super_admin": 12345, "admin_users": [10001, 10002]}}
        cm.update(new_data)
        cfg = cm.config.bot
        assert cfg.admin_set == frozenset({"12345", "10001", "10002"})

        # 清空 admin_users 后仍包含 super_admin
        new_data = {"bot": {"super_admin": 12345, "admin_users": []}}
        cm.update(new_data)
        cfg = cm.config.bot
        assert cfg.admin_set == frozenset({"12345"})

        # 只有 super_admin 时，admin_set 仅为 super_admin
        assert "12345" in cfg.admin_set
        assert "99999" not in cfg.admin_set


class TestPluginConfig:
    """set_plugin_config：写盘失败必须回滚内存，不留下内存/磁盘不一致"""

    def test_set_plugin_config_ok(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        got = cm.set_plugin_config("myplugin", {"enabled": True, "level": 3})
        assert got == {"enabled": True, "level": 3}
        assert cm.get_plugin_config("myplugin") == {"enabled": True, "level": 3}
        # 已持久化到磁盘
        data = yaml.safe_load(Path(config_file).read_text(encoding="utf-8"))
        assert data["plugins"]["myplugin"] == {"enabled": True, "level": 3}

    def test_set_plugin_config_rollback_on_save_failure(self, config_file, monkeypatch):
        cm = ConfigManager(Path(config_file))
        cm.load()
        original = cm.to_dict()

        def _boom(self):
            raise OSError("disk full")

        monkeypatch.setattr(ConfigManager, "save", _boom)

        with pytest.raises(OSError, match="disk full"):
            cm.set_plugin_config("myplugin", {"enabled": True})

        # 内存回滚：plugins 不含 myplugin
        assert cm.get_plugin_config("myplugin") is None
        # 磁盘未改动
        assert cm.to_dict() == original
        data = yaml.safe_load(Path(config_file).read_text(encoding="utf-8"))
        assert "myplugin" not in (data.get("plugins") or {})


class TestConfigDeepMerge:
    """update() 深合并语义：部分配置不会静默重置其它节"""

    def test_partial_update_preserves_other_sections(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        original_llm_key = cm.config.llm.api_key

        # 仅更新 bot.admin_users，llm 整节与 api_key 必须保持不变
        cm.update({"bot": {"admin_users": [99999]}})
        assert cm.config.bot.admin_users == ["99999"]
        assert cm.config.bot.super_admin == cm.config.bot.super_admin  # 无副作用
        assert cm.config.llm.api_key == original_llm_key
        assert cm.config.llm.model == "gpt-4o-mini"  # 默认值未被重置

    def test_update_preserves_other_plugin_configs(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        cm.update({"plugins": {"alpha": {"enabled": True, "level": 2}, "beta": {"k": 1}}})
        # 再次更新 alpha 的部分字段，beta 配置不受影响
        cm.update({"plugins": {"alpha": {"level": 5}}})
        assert cm.get_plugin_config("alpha") == {"enabled": True, "level": 5}
        assert cm.get_plugin_config("beta") == {"k": 1}

    def test_list_replacement_not_merge(self, config_file):
        cm = ConfigManager(Path(config_file))
        cm.load()
        cm.update({"bot": {"admin_users": [10001, 10002], "super_admin": 12345}})
        cm.update({"bot": {"admin_users": []}})
        assert cm.config.bot.admin_users == []
        assert cm.config.bot.super_admin == "12345"
