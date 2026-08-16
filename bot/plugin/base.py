"""插件基类 — 转发至独立插件 SDK

协议层（PluginBase / PluginStatus）统一由 qingci_plugin_sdk.base 维护，
主项目不再维护副本，避免两处定义漂移。内置插件与外部插件共用同一基类：
- data_dir 经 SDK paths.data_root() 解析，PluginManager 加载时已将 SDK 数据根
  重定向到当前实例（见 manager._load_or_reload），行为与内置一致
- 旧式 on_message/on_notice/on_request 已标记 deprecated，新插件请用 Matcher
"""

from qingci_plugin_sdk.base import *  # noqa: F401,F403
from qingci_plugin_sdk.base import PluginBase, PluginStatus  # noqa: F401

__all__ = ["PluginBase", "PluginStatus"]
