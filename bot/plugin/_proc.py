# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

"""子进程执行辅助（跨平台隐藏控制台窗口）

桌面壳（Electron 拉起的后端、无控制台窗口/无头）模式下，asyncio
create_subprocess_exec 启动 git / uv / pip 等控制台程序时，Windows 会为
新进程创建可见的 cmd 窗口。统一通过本模块传入
``creationflags=NO_WINDOW_FLAG`` 隐藏窗口。

用法::

    from ._proc import NO_WINDOW_FLAG

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=NO_WINDOW_FLAG,
    )

需要统一的超时/退出码处理时，直接用 ``run_command``（返回 ``CommandResult``），
不要在调用方复制 create_subprocess_exec + wait_for + kill 的模板::

    from ._proc import run_command

    result = await run_command(["git", "clone", "--depth", "1", url, str(tmp)])
    if result.timed_out:
        ...
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass

if sys.platform == "win32":
    #: 0x08000000：不创建控制台窗口（子进程无独立 cmd 窗口）
    NO_WINDOW_FLAG: int = subprocess.CREATE_NO_WINDOW
else:
    NO_WINDOW_FLAG = 0


@dataclass(frozen=True)
class CommandResult:
    """子进程执行结果；``timed_out`` 为真时 ``returncode`` 为 None。"""

    stdout: bytes
    returncode: int | None
    timed_out: bool = False


async def run_command(cmd: list[str], *, timeout: float = 300) -> CommandResult:
    """执行命令、合并 stdout/stderr 并在超时后终止子进程。

    统一 installer / market 等调用方的窗口隐藏与超时控制流：超时、非零退出
    都以 ``CommandResult`` 返回，由调用方自行决定「返回 False」还是「抛业务异常」，
    避免两处复制相同的 subprocess 模板。

    无法启动（``OSError`` / ``ValueError``）时原样抛出，交由调用方处理。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=NO_WINDOW_FLAG,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return CommandResult(stdout=b"", returncode=None, timed_out=True)
    return CommandResult(stdout=stdout or b"", returncode=proc.returncode)
