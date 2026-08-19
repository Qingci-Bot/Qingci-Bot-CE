"""权限系统 — 转发至独立插件 SDK

协议层（Permission / 内置权限常量）统一由 qingci_plugin_sdk.permission 维护，
主项目不再维护副本，避免两处定义漂移。外部插件与内置插件共用同一套权限语义。
"""

from qingci_plugin_sdk.permission import *  # noqa: F401,F403
from qingci_plugin_sdk.permission import (  # noqa: F401
    ADMIN,
    EVERYONE,
    GROUP,
    GROUP_MEMBER,
    MEMBER,
    PRIVATE,
    SUPERUSER,
    USER,
    Permission,
    describe_permission,
)

__all__ = [
    "Permission",
    "EVERYONE",
    "SUPERUSER",
    "ADMIN",
    "PRIVATE",
    "GROUP",
    "MEMBER",
    "USER",
    "GROUP_MEMBER",
    "describe_permission",
]
