"""API 端点测试（使用 FastAPI TestClient）"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

# 确保测试加载时不会触发真实 Bot 启动
os.environ.setdefault("QINGCI_TEST", "1")


@pytest.fixture
def test_config():
    """创建临时配置文件"""
    config = {
        "api_key": "test-api-key-123",
        "bot": {
            "admin_users": [],
            "log_json": False,
        },
        "onebot": {
            "host": "127.0.0.1",
            "port": 3001,
            "access_token": "",
        },
        "llm": {
            "provider": "openai",
            "api_url": "https://api.openai.com/v1",
            "api_key": "",
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


@pytest.fixture
def client(test_config):
    """创建测试客户端"""
    from api.auth import set_config_path

    set_config_path(Path(test_config))

    from api.server import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _auth_headers():
    return {"X-API-Key": "test-api-key-123"}


class TestHealthEndpoint:
    """健康检查端点测试"""

    def test_health_returns_200(self, client):
        resp = client.get("/api/bot/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data


class TestAuthRequired:
    """鉴权测试"""

    def test_no_api_key_returns_401(self, client):
        resp = client.get("/api/config")  # config 路由需要鉴权
        assert resp.status_code == 401

    def test_wrong_api_key_returns_401(self, client):
        resp = client.get("/api/config", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_correct_api_key_succeeds(self, client):
        resp = client.get("/api/config", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert "api_key" in data
        # 敏感字段应被遮蔽
        assert data["api_key"] == "***"


class TestBotStatus:
    """Bot 状态端点测试"""

    def test_status_returns_200(self, client):
        resp = client.get("/api/bot/status", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "connected" in data
        assert "plugins" in data
        assert isinstance(data["plugins"], list)
        # 插件条目须携带 pages 字段（WebUI 卡片 Web 管理入口按钮依赖）
        for p in data["plugins"]:
            assert "pages" in p and isinstance(p["pages"], list)
        # 平台状态：结构校验；运行中时至少包含默认 onebot 平台
        assert "platforms" in data
        assert isinstance(data["platforms"], list)
        for p in data["platforms"]:
            assert {"name", "display_name", "connected", "last_heartbeat", "self_id"} <= set(
                p.keys()
            )
        if data["running"]:
            assert any(p["name"] == "onebot" for p in data["platforms"])


class TestLogEndpoint:
    """日志端点测试"""

    def test_log_messages_requires_bot(self, client):
        # Bot 未启动时返回 503
        resp = client.get("/api/log/messages", headers=_auth_headers())
        assert resp.status_code == 503

    def test_log_count_requires_bot(self, client):
        # Bot 未启动时返回 503
        resp = client.get("/api/log/messages/count", headers=_auth_headers())
        assert resp.status_code == 503


class TestPluginEndpoint:
    """插件端点测试"""

    def test_plugin_list_returns_503_without_bot(self, client):
        # Bot 未启动时返回 503
        resp = client.get("/api/plugin", headers=_auth_headers())
        assert resp.status_code == 503


class TestRootRedirect:
    """根路径重定向测试"""

    def test_root_redirects(self, client):
        resp = client.get("/", follow_redirects=False)
        # 可能 307 重定向到 /ui 或 200（无 Web UI 构建产物时）
        assert resp.status_code in (200, 307)
