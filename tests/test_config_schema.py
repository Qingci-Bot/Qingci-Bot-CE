# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""配置 Schema 生成器契约测试（P1）

锁四个契约：
1. schema 顶层 properties 与 AppConfig 字段一一对应（模型加字段 → schema 跟随）
2. 敏感字段统一带 x-secret 标记 + format=password（前端渲染依据）
3. config.example.yaml 的键全部被 schema 覆盖（示例写错键即可发现）
4. GET /api/config/schema 端点可用且鉴权对齐
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bot.config import AppConfig
from bot.config_schema import build_config_schema, is_secret_field

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def _schema() -> dict:
    return build_config_schema()


def test_schema_top_level_matches_model_fields():
    """顶层 properties 与 AppConfig.model_fields 一一对应"""
    schema = _schema()
    props = schema["properties"]
    assert set(props) == set(AppConfig.model_fields), (
        f"schema 字段与模型不一致: {set(props) ^ set(AppConfig.model_fields)}"
    )


def test_schema_has_draft_version_and_defs():
    """schema 带 $schema 声明；嵌套模型在 $defs 且与模型子类对应"""
    schema = _schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    def_names = set(schema.get("$defs") or {})
    # 抽查若干已知嵌套模型
    for name in ("BotConfig", "LLMConfig", "RenderConfig", "PlatformsConfig"):
        assert name in def_names, f"$defs 缺少嵌套模型 {name}"


def test_schema_field_metadata_from_model():
    """字段类型 / 默认值 / 描述来自模型（抽查）"""
    schema = _schema()
    bot_prop = schema["properties"]["bot"]
    assert bot_prop.get("$ref") or bot_prop.get("type")
    lang = schema["properties"]["lang"]
    assert lang.get("type") == "string"
    assert lang.get("default") == "zh-CN"


def test_sensitive_fields_marked():
    """敏感字段带 x-secret + format=password（顶层与 $defs 内都要标）"""
    schema = _schema()
    # 顶层：api_key
    assert schema["properties"]["api_key"].get("x-secret") is True
    assert schema["properties"]["api_key"].get("format") == "password"
    # $defs：llm.api_key / onebot.access_token / telegram.bot_token
    defs = schema["$defs"]
    assert defs["LLMConfig"]["properties"]["api_key"].get("x-secret") is True
    assert defs["OneBotConfig"]["properties"]["access_token"].get("x-secret") is True
    assert defs["TelegramConfig"]["properties"]["token"].get("x-secret") is True
    # 非敏感字段不得误标
    assert "x-secret" not in defs["BotConfig"]["properties"]["name"]


def test_is_secret_field_heuristics():
    """敏感判定启发式（与插件配置脱敏共用同一实现）"""
    for name in ("api_key", "access_token", "bot_token", "PASSWORD", "openai_api_key"):
        assert is_secret_field(name), name
    for name in ("model", "timeout", "enabled", "host", "port"):
        assert not is_secret_field(name), name


def test_example_yaml_keys_covered_by_schema():
    """config.example.yaml 的键全部在 schema properties 中（含嵌套）"""

    def check(data: dict, node: dict, path: str) -> None:
        props = node.get("properties") or {}
        unknown = set(data) - set(props)
        assert not unknown, f"{path} 存在 schema 未覆盖的键: {sorted(unknown)}"
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            prop = props.get(key) or {}
            ref = prop.get("$ref", "")
            def_name = ref.rsplit("/", 1)[-1] if ref else None
            child = (_schema_defs().get(def_name) if def_name else None) or {}
            if child.get("type") == "object":
                check(value, child, f"{path}.{key}")

    example = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    defs = _schema_defs()
    for section, section_value in (example or {}).items():
        if not isinstance(section_value, dict):
            continue
        prop = _schema()["properties"].get(section) or {}
        ref = prop.get("$ref", "")
        def_name = ref.rsplit("/", 1)[-1] if ref else None
        child = defs.get(def_name) or {}
        if child.get("type") == "object":
            check(section_value, child, section)


def _schema_defs() -> dict:
    return _schema().get("$defs") or {}


@pytest.fixture
def client(tmp_path):
    """带鉴权的测试客户端（同 test_api.py 的隔离方式）"""
    import os

    os.environ.setdefault("QINGCI_TEST", "1")
    config = {
        "api_key": "test-api-key-123",
        "bot": {"admin_users": []},
        "llm": {"provider": "openai", "api_url": "https://api.openai.com/v1", "api_key": ""},
        "render": {"enabled": False},
        "rag": {"enabled": False},
        "scheduler": {"enabled": False},
        "alert": {"enabled": False},
        "rate_limit": {"enabled": False},
        "session_summary": {"enabled": False},
        "session_state": {"enabled": False},
        "hot_reload": {"enabled": False},
        "log": {"log_file_enabled": False},
    }
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".yaml", dir=tmp_path)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.dump(config, f)

    from api.auth import set_config_path

    set_config_path(Path(path))
    from api.server import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    try:
        os.unlink(path)
    except OSError:
        pass


def test_schema_endpoint_requires_auth(client):
    resp = client.get("/api/config/schema")
    assert resp.status_code == 401


def test_schema_endpoint_returns_schema(client):
    resp = client.get("/api/config/schema", headers={"X-API-Key": "test-api-key-123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "bot" in data["properties"]
    assert data["properties"]["api_key"]["x-secret"] is True
