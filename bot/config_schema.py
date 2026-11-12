# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""配置 Schema 生成器（P1）

把 Pydantic 配置模型（AppConfig）导出为 JSON Schema，作为 WebUI 配置表单的
单一事实来源：

- 字段列表 / 类型 / 默认值 / 描述全部来自模型定义，前端不再手抄字段；
- 敏感字段统一打 ``x-secret`` 标记（+ ``format: password``），前端据此渲染
  密码输入框并做空值保护（提交空值时保留已有值，语义与插件配置脱敏一致）；
- ``GET /api/config/schema`` 对外输出（见 api/routes/config.py）。

敏感字段判定沿用插件配置脱敏的同名启发式（token/secret/password/api_key），
两处共用 ``is_secret_field``，避免双份规则漂移。
"""

from __future__ import annotations

from .config import AppConfig

# 敏感字段名启发式（与插件配置脱敏共用；大小写不敏感）
_SECRET_HINTS = ("token", "secret", "password", "api_key", "apikey")


def is_secret_field(name: str) -> bool:
    """判断配置字段名是否敏感（用于 schema 标记与响应脱敏，判定规则两处共用）"""
    lowered = str(name).casefold()
    return any(hint in lowered for hint in _SECRET_HINTS)


def _mark_secret_fields(node: dict) -> None:
    """给 object 节点的敏感属性打 x-secret 标记（供前端渲染密码输入框）"""
    for name, prop in (node.get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        if is_secret_field(name):
            prop["x-secret"] = True
            prop.setdefault("format", "password")


def build_config_schema() -> dict:
    """生成 AppConfig 的 JSON Schema（draft 2020-12）

    - 嵌套模型位于 ``$defs``（pydantic v2 导出约定），同样做敏感标记；
    - 顶层 ``title`` / ``description`` 保留模型 docstring（中文说明直出前端）。
    """
    schema: dict = AppConfig.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"

    nodes = [schema]
    for defn in (schema.get("$defs") or {}).values():
        if isinstance(defn, dict):
            nodes.append(defn)
    for node in nodes:
        if node.get("type") == "object":
            _mark_secret_fields(node)

    return schema
