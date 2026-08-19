"""插件级 Web API 注册机制测试

验证 PluginBase.register_api → PluginManager 收集 → FastAPI 挂载
（/api/plugin-web/<name>/<path>）全链路，覆盖返回类型归一化、鉴权、
插件卸载/热重载后的动态解析。
"""

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

# 确保测试加载时不会触发真实 Bot 启动
os.environ.setdefault("QINGCI_TEST", "1")


def _write_config(tmp_path: Path, api_key: str) -> Path:
    """写入临时配置并设置鉴权配置路径"""
    from api.auth import set_config_path

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"api_key": api_key, "bot": {"admin_users": []}}, allow_unicode=True),
        encoding="utf-8",
    )
    set_config_path(cfg_path)
    return cfg_path


async def _build_client(tmp_path: Path, api_key: str = ""):
    """加载测试插件并挂载到 FastAPI 应用，返回 (TestClient, TestBot)"""
    from bot.testing import TestBot

    _write_config(tmp_path, api_key)
    bot = TestBot()
    assert await bot.load_plugin("plugin_pkg.webapi_plugin")

    from api.server import create_app

    app = create_app()
    bot.plugin_manager.set_web_app(app)
    client = TestClient(app)
    return client, bot


@pytest.fixture
async def webapi_app(tmp_path):
    """加载注册了 Web API 的测试插件并挂载（api_key 留空，跳过鉴权）"""
    client, bot = await _build_client(tmp_path)
    with client:
        yield client, bot


# ──────────────────────────────────────────────────────────────────
# 基础挂载与返回类型归一化
# ──────────────────────────────────────────────────────────────────


async def test_ping_returns_json(webapi_app):
    """GET 返回 dict 自动 JSON 序列化"""
    client, bot = webapi_app
    resp = client.get("/api/plugin-web/webapi/ping")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pong"] is True
    assert body["plugin"] == "webapi"
    assert body["inst_id"] == id(bot.plugin_manager.get("webapi"))


async def test_echo_returns_status_tuple(webapi_app):
    """POST 返回 (data, status) 二元组：JSON 序列化并指定状态码"""
    client, _ = webapi_app
    resp = client.post("/api/plugin-web/webapi/echo", json={"x": 1})
    assert resp.status_code == 201
    assert resp.json() == {"echo": {"x": 1}}


async def test_raw_response_passthrough(webapi_app):
    """handler 返回 Response 对象时原样透传"""
    client, _ = webapi_app
    resp = client.get("/api/plugin-web/webapi/raw")
    assert resp.status_code == 200
    assert resp.json() == {"raw": 1}


async def test_handler_error_returns_500(webapi_app):
    """handler 抛异常：统一转 500 JSON，不泄漏堆栈"""
    client, _ = webapi_app
    resp = client.get("/api/plugin-web/webapi/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "插件 Web API 内部错误"
    assert body["error_type"] == "RuntimeError"


async def test_unknown_path_returns_404(webapi_app):
    """未注册路径返回 404（路由未匹配，FastAPI 默认 404）"""
    client, _ = webapi_app
    resp = client.get("/api/plugin-web/webapi/nope")
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────
# 鉴权
# ──────────────────────────────────────────────────────────────────


async def test_auth_required(tmp_path):
    """配置了 api_key 时，插件 Web API 对齐现有鉴权体系"""
    client, _ = await _build_client(tmp_path, api_key="secret-key")
    with client:
        # 未带 X-API-Key → 401
        resp = client.get("/api/plugin-web/webapi/ping")
        assert resp.status_code == 401
        # 带正确 X-API-Key → 200
        resp = client.get("/api/plugin-web/webapi/ping", headers={"X-API-Key": "secret-key"})
        assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────
# 卸载 / 热重载
# ──────────────────────────────────────────────────────────────────


async def test_unload_plugin_returns_404(webapi_app):
    """插件卸载后，已挂载路由返回 404（动态解析插件实例）"""
    client, bot = webapi_app
    await bot.plugin_manager.unload("webapi")
    resp = client.get("/api/plugin-web/webapi/ping")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "插件未加载"


async def test_reload_plugin_serves_new_handler(webapi_app):
    """插件热重载后，旧路由动态解析到新实例（不重复挂载、不失效）"""
    client, bot = webapi_app

    old_inst = bot.plugin_manager.get("webapi")
    await bot.plugin_manager.reload("webapi", bot)
    new_inst = bot.plugin_manager.get("webapi")
    assert new_inst is not old_inst  # 重载确实生成了新实例

    resp = client.get("/api/plugin-web/webapi/ping")
    assert resp.status_code == 200
    body = resp.json()
    # 路由解析到新实例的 handler（而非旧闭包）
    assert body["inst_id"] == id(new_inst)
    assert body["inst_id"] != id(old_inst)


async def test_mount_is_idempotent(webapi_app):
    """重复挂载同路径不会产生重复路由"""
    client, bot = webapi_app
    before = len(client.app.routes)
    bot.plugin_manager.set_web_app(client.app)
    after = len(client.app.routes)
    assert after == before

    # 路由仍正常响应
    resp = client.get("/api/plugin-web/webapi/ping")
    assert resp.status_code == 200


async def test_plugin_namespace_has_no_stray_routes(webapi_app):
    """仅挂载已注册的 API 路径，无残留路由"""
    client, _ = webapi_app
    paths = [getattr(r, "path", None) for r in client.app.routes]
    assert "/api/plugin-web/webapi/ping" in paths
    assert "/api/plugin-web/webapi/echo" in paths
    assert not any(p == "/api/plugin-web/webapi/nonexistent" for p in paths)
