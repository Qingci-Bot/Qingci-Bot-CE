# Copyright (C) 2026 Zhou Zhe (aka luoqingci) and Qingci-Bot contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

from .manager import PluginManager
from .protocol.base import PluginBase, PluginStatus
from .protocol.matcher import (
    Matcher,
    MatcherContext,
    on_command,
    on_keyword,
    on_message,
    on_notice,
    on_request,
    on_startswith,
)
from .protocol.permission import (
    ADMIN,
    EVERYONE,
    GROUP,
    GROUP_MEMBER,
    MEMBER,
    PRIVATE,
    SUPERUSER,
    USER,
    Permission,
)
from .protocol.rule import (
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
    to_me,
)

__all__ = [
    # 基础
    "PluginBase",
    "PluginStatus",
    "PluginManager",
    # Matcher
    "Matcher",
    "MatcherContext",
    "on_message",
    "on_command",
    "on_startswith",
    "on_keyword",
    "on_notice",
    "on_request",
    # Permission
    "Permission",
    "EVERYONE",
    "SUPERUSER",
    "ADMIN",
    "PRIVATE",
    "GROUP",
    "MEMBER",
    "USER",
    "GROUP_MEMBER",
    # Rule
    "Rule",
    "startswith",
    "endswith",
    "fullmatch",
    "contains",
    "regex",
    "command",
    "to_me",
    "is_private",
    "is_group",
    "keyword",
    "rate_limit",
]
