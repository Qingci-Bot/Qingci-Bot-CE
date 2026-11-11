# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

"""匹配器系统 — 协议层转发（兼容别名）

协议层唯一实现来源为 Plugins-SDK，实际转发位于
`bot/plugin/protocol/matcher.py`。本文件保留为兼容再导出，供存量导入路径
`from bot.plugin.matcher import ...` 继续使用；新代码请直接导入
`bot.plugin.protocol.matcher`。
"""

from .protocol.matcher import *  # noqa: F401,F403
from .protocol.matcher import __all__  # noqa: F401
