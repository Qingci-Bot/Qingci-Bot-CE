"""限流器 — 转发至独立插件 SDK

协议层（RateLimiter）统一由 qingci_plugin_sdk.ratelimit 维护，
主项目不再维护副本，避免两处定义漂移。
"""

from qingci_plugin_sdk.ratelimit import *  # noqa: F401,F403
from qingci_plugin_sdk.ratelimit import RateLimiter  # noqa: F401

__all__ = ["RateLimiter"]
