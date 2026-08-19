"""类型化事件 — 协议层转发（兼容别名）

协议层唯一实现来源为 Plugins-SDK，实际转发位于
`bot/plugin/protocol/events.py`。本文件保留为兼容再导出，供存量导入路径
`from bot.plugin.events import ...` 继续使用；新代码请直接导入
`bot.plugin.protocol.events`。
"""

from .protocol.events import *  # noqa: F401,F403
from .protocol.events import __all__  # noqa: F401
