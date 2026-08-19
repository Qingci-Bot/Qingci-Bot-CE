"""规则系统 — 转发至独立插件 SDK

协议层（Rule / 内置规则工厂）统一由 qingci_plugin_sdk.rule 维护，
主项目不再维护副本，避免两处定义漂移。
"""

from qingci_plugin_sdk.rule import *  # noqa: F401,F403
from qingci_plugin_sdk.rule import (  # noqa: F401
    Rule,
    command,
    contains,
    endswith,
    fullmatch,
    is_group,
    is_private,
    keyword,
    rate_limit,
    regex,
    startswith,
    subcommand,
    to_me,
)

__all__ = [
    "Rule",
    "startswith",
    "endswith",
    "fullmatch",
    "contains",
    "regex",
    "command",
    "subcommand",
    "to_me",
    "is_private",
    "is_group",
    "keyword",
    "rate_limit",
]
