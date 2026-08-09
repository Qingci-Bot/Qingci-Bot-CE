from .base import PluginBase
from .manager import PluginManager
from .matcher import (
    Matcher,
    MatcherContext,
    on_message,
    on_command,
    on_startswith,
    on_keyword,
    on_notice,
    on_request,
)
from .permission import (
    Permission,
    EVERYONE,
    SUPERUSER,
    ADMIN,
    PRIVATE,
    GROUP,
    MEMBER,
    USER,
    GROUP_MEMBER,
)
from .rule import (
    Rule,
    startswith,
    endswith,
    fullmatch,
    contains,
    regex,
    command,
    to_me,
    is_private,
    is_group,
    keyword,
)

__all__ = [
    # 基础
    "PluginBase",
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