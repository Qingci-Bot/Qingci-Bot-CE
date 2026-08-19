"""登录与鉴权状态路由测试（/api/auth/status, /api/auth/login）

复用 test_api 的 client fixture 模式：set_config_path + create_app + TestClient。
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean_login_failures():
    """每个测试前清空登录防暴力计数，避免跨测试残留"""
    from api.routes import login as auth_routes

    auth_routes._login_failures.clear()
    yield
    auth_routes._login_failures.clear()


@pytest.fixture
def client():
    """创建临时配置 + 测试客户端"""
    config = {
        "api_key": "correct-key-999",
        "bot": {"admin_users": [], "log_json": False},
        "onebot": {"host": "127.0.0.1", "port": 3001, "access_token": ""},
        "llm": {"provider": "openai", "api_url": "", "api_key": "", "model": "gpt-4o-mini"},
        "log": {"usage_tracking": False},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(config, f)
        path = f.name
    try:
        from api.auth import set_config_path

        set_config_path(Path(path))
        from api.server import create_app

        with TestClient(create_app()) as c:
            yield c
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class TestAuthStatus:
    def test_status_requires_login_when_key_set(self, client):
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {"auth_required": True}


class TestLogin:
    def test_login_success(self, client):
        resp = client.post("/api/auth/login", json={"api_key": "correct-key-999"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_login_wrong_key_returns_401(self, client):
        resp = client.post("/api/auth/login", json={"api_key": "wrong"})
        assert resp.status_code == 401

    def test_login_empty_key_returns_401(self, client):
        resp = client.post("/api/auth/login", json={"api_key": ""})
        assert resp.status_code == 401

    def test_login_rate_limit_after_failures(self, client):
        # 连续失败达阈值（5 次）后，冷却期内第 6 次返回 429
        for _ in range(5):
            assert client.post("/api/auth/login", json={"api_key": "wrong"}).status_code == 401
        resp = client.post("/api/auth/login", json={"api_key": "wrong"})
        assert resp.status_code == 429

    def test_successful_login_resets_failure_count(self, client):
        # 失败 3 次后成功登录，清零计数；再次失败不会立刻触发 429
        for _ in range(3):
            assert client.post("/api/auth/login", json={"api_key": "wrong"}).status_code == 401
        assert (
            client.post("/api/auth/login", json={"api_key": "correct-key-999"}).status_code == 200
        )
        # 清零后重新失败，仍未达阈值
        for _ in range(4):
            resp = client.post("/api/auth/login", json={"api_key": "wrong"})
            assert resp.status_code == 401
