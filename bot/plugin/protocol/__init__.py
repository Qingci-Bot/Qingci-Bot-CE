"""插件协议层（唯一实现来源为 Plugins-SDK）

协议层（PluginBase / Matcher / Rule / Permission / MessageContext /
类型化事件 / Session / RateLimiter）统一由独立插件 SDK
（qingci_plugin_sdk）维护，本子包内的模块均为**薄转发**，主项目不保存
任何协议实现。

- 框架内部代码应直接引用本子包（`bot.plugin.protocol.*`）；
- `bot/plugin/` 顶层的同名模块（base/matcher/rule/...）为**兼容再导出**，
  供存量导入路径与外部插件继续使用；
- 修改协议行为一律改 Plugins-SDK，本子包只做转发。
"""

from .base import PluginBase, PluginStatus
from .context import MessageContext
from .matcher import (
    Matcher,
    MatcherContext,
    on_command,
    on_keyword,
    on_message,
    on_notice,
    on_request,
    on_startswith,
)
from .permission import (
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
from .rule import (
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
    # 基础
    "PluginBase",
    "PluginStatus",
    "MessageContext",
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
    "subcommand",
    "to_me",
    "is_private",
    "is_group",
    "keyword",
    "rate_limit",
]
