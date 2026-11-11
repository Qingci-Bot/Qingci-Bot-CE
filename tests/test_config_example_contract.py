# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""P1 第二阶段：config.example.yaml 与 AppConfig 的契约锁

示例文件带大量手写注释（给用户的配置说明），刻意不做生成替换；
改用契约测试保证模型增删字段、示例写错键都能被立即发现。

注意 Pydantic 默认 ``extra='ignore'``，示例里写错的键会被静默忽略，
所以必须显式检查未知键，不能只依赖 ``AppConfig(**data)`` 解析成功。
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Union, get_args, get_origin

import pytest
import yaml
from pydantic import BaseModel

from bot.config import AppConfig

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """取字段注解里的嵌套 BaseModel（剥掉 Optional / Union）"""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        candidates = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(candidates) == 1 and isinstance(candidates[0], type):
            if issubclass(candidates[0], BaseModel):
                return candidates[0]
    return None


def _unknown_keys(model: type[BaseModel], obj: Any, path: str = "") -> list[str]:
    if not isinstance(obj, dict):
        return []
    unknown: list[str] = []
    for key, value in obj.items():
        current = f"{path}.{key}" if path else str(key)
        field = model.model_fields.get(key)
        if field is None:
            unknown.append(current)
            continue
        nested = _nested_model(field.annotation)
        if nested is not None:
            unknown.extend(_unknown_keys(nested, value, current))
    return unknown


def _missing_required(model: type[BaseModel], obj: Any, path: str = "") -> list[str]:
    if not isinstance(obj, dict):
        return []
    missing: list[str] = []
    for name, field in model.model_fields.items():
        current = f"{path}.{name}" if path else name
        if field.is_required() and name not in obj:
            missing.append(current)
        nested = _nested_model(field.annotation)
        if nested is not None and name in obj:
            missing.extend(_missing_required(nested, obj[name], current))
    return missing


@pytest.fixture(scope="module")
def example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_example_parses_as_app_config(example):
    config = AppConfig(**example)
    assert config.bot.name == "Qingci-Bot CE"
    assert config.lang == "zh-CN"
    assert config.plugins == {}


def test_example_top_level_sections_cover_all_model_fields(example):
    """模型新增顶层配置节时必须同步补示例，否则用户看不到该配置项"""
    assert set(example) == set(AppConfig.model_fields)


def test_example_has_no_unknown_keys(example):
    assert _unknown_keys(AppConfig, example) == []


def test_example_has_no_missing_required_fields(example):
    assert _missing_required(AppConfig, example) == []
