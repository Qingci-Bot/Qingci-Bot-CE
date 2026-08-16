"""实例管理 API 路由测试（/api/instances*）

通过 monkeypatch 将实例注册表与 data_root 重定向到临时目录，
隔离实例创建/删除/重命名/启动的真实副作用。
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    """临时配置 + 隔离实例目录 + 测试客户端"""
    config = {
        "api_key": "test-key",
        "bot": {"admin_users": [], "log_json": False},
        "onebot": {"host": "127.0.0.1", "port": 3001, "access_token": ""},
        "llm": {"provider": "openai", "api_url": "", "api_key": "", "model": "gpt-4o-mini"},
        "log": {"usage_tracking": False},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(config, f)
        cfg_path = f.name

    # 重定向实例注册表到临时目录
    import bot.instances as inst

    monkeypatch.setattr(inst, "instances_dir", lambda: tmp_path)
    # 当前运行实例标记：data_root 指向 tmp_path 下不存在的 data，视为无运行实例
    monkeypatch.setattr("api.routes.instances.data_root", lambda: tmp_path / "nowhere")

    try:
        from api.auth import set_config_path

        set_config_path(Path(cfg_path))
        from api.server import create_app

        with TestClient(create_app()) as c:
            yield c
    finally:
        try:
            os.unlink(cfg_path)
        except OSError:
            pass


def _headers():
    return {"X-API-Key": "test-key"}


class TestListInstances:
    def test_empty_list(self, client):
        resp = client.get("/api/instances", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_create(self, client):
        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        resp = client.get("/api/instances", headers=_headers())
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "alpha"
        assert data[0]["running"] is False


class TestCreateInstance:
    def test_create_returns_201(self, client):
        resp = client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        assert resp.status_code == 201
        assert resp.json()["name"] == "alpha"

    def test_create_duplicate_returns_400(self, client):
        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        resp = client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        assert resp.status_code == 400

    def test_create_invalid_name_returns_400(self, client):
        resp = client.post("/api/instances", json={"name": "../escape"}, headers=_headers())
        assert resp.status_code == 400


class TestDeleteInstance:
    def test_delete_ok(self, client):
        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        resp = client.delete("/api/instances/alpha", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_delete_missing_returns_404(self, client):
        resp = client.delete("/api/instances/ghost", headers=_headers())
        assert resp.status_code == 404

    def test_delete_running_returns_400(self, client, monkeypatch, tmp_path):
        # 让 data_root 指向 alpha 实例的 data 目录，使其成为"运行中"实例
        from bot.instances import instance_path

        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        monkeypatch.setattr(
            "api.routes.instances.data_root",
            lambda: instance_path("alpha") / "data",
        )
        resp = client.delete("/api/instances/alpha", headers=_headers())
        assert resp.status_code == 400


class TestRenameInstance:
    def test_rename_ok(self, client):
        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        resp = client.put("/api/instances/alpha", json={"new_name": "beta"}, headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["name"] == "beta"

    def test_rename_missing_returns_404(self, client):
        resp = client.put("/api/instances/ghost", json={"new_name": "beta"}, headers=_headers())
        assert resp.status_code == 404

    def test_rename_invalid_returns_400(self, client):
        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        resp = client.put("/api/instances/alpha", json={"new_name": "../x"}, headers=_headers())
        assert resp.status_code == 400

    def test_rename_to_existing_returns_400(self, client):
        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        client.post("/api/instances", json={"name": "beta"}, headers=_headers())
        resp = client.put("/api/instances/alpha", json={"new_name": "beta"}, headers=_headers())
        assert resp.status_code == 400


class TestStartInstance:
    def test_start_missing_returns_404(self, client):
        resp = client.post("/api/instances/ghost/start", headers=_headers())
        assert resp.status_code == 404

    def test_start_running_returns_400(self, client, monkeypatch):
        from bot.instances import instance_path

        client.post("/api/instances", json={"name": "alpha"}, headers=_headers())
        monkeypatch.setattr(
            "api.routes.instances.data_root",
            lambda: instance_path("alpha") / "data",
        )
        resp = client.post("/api/instances/alpha/start", headers=_headers())
        assert resp.status_code == 400

    def test_start_swap_does_not_exit_test_process(self, client, monkeypatch):
        """切换到另一实例会调用 os._exit 终止进程，测试中必须 mock 掉"""
        client.post("/api/instances", json={"name": "a"}, headers=_headers())
        client.post("/api/instances", json={"name": "b"}, headers=_headers())
        # 让 data_root 指向 a 实例，当前运行 a；切换到 b 不会命中"已运行"分支
        from bot.instances import instance_path

        monkeypatch.setattr(
            "api.routes.instances.data_root",
            lambda: instance_path("a") / "data",
        )
        monkeypatch.setattr("api.routes.instances.spawn_relaunch", lambda *a, **k: None)
        monkeypatch.setattr("api.routes.instances.os._exit", lambda code: None)
        resp = client.post("/api/instances/b/start", headers=_headers())
        assert resp.status_code == 200
