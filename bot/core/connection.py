"""OneBot 11 平台适配器 — 兼容别名（实际实现在 platforms/onebot11.py）

B2 结构调整：OneBot 11 反向 WS 实现已归入多平台适配器目录
（bot/core/platforms/onebot11.py，与 onebot12/telegram 对齐）。本模块
保留为兼容再导出，供存量代码 `from bot.core.connection import OneBotConnection`
继续使用；新代码请直接导入 `bot.core.platforms.onebot11`。
"""

from .platforms.onebot11 import *  # noqa: F401,F403
from .platforms.onebot11 import OneBotConnection  # noqa: F401

__all__ = ["OneBotConnection"]
