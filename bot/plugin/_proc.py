"""子进程执行辅助（跨平台隐藏控制台窗口）

GUI 桌面模式（pywebview + pythonw）下，asyncio.create_subprocess_exec 启动
git / uv / pip 等控制台程序时，Windows 会为新进程创建可见的 cmd 窗口。
统一通过本模块传入 ``creationflags=NO_WINDOW_FLAG`` 隐藏窗口。

用法::

    from ._proc import NO_WINDOW_FLAG

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=NO_WINDOW_FLAG,
    )
"""

from __future__ import annotations

import subprocess
import sys

if sys.platform == "win32":
    #: 0x08000000：不创建控制台窗口（子进程无独立 cmd 窗口）
    NO_WINDOW_FLAG: int = subprocess.CREATE_NO_WINDOW
else:
    NO_WINDOW_FLAG = 0
