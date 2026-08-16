"""跨进程重启助手

问题：os._exit() 立即终止进程，同一进程内的后台线程（即使 daemon=True）
也会被一并杀死，无法"等旧进程退出后再拉起新进程"。因此切换实例/运行中
改名不再用线程，而是派发一个**独立分离进程**作为助手：助手存活于主进程之外，
等待旧进程退出（释放 DB 文件锁）后，可选地执行目录改名，再拉起目标实例。

用法：
    spawn_relaunch(app_args)          # 当前进程派发助手并立即退出
    run_helper_if_requested() -> bool # main() 入口最早调用；命中助手模式返回 True
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

_RELAUNCH_FLAG = "--relaunch-wait"
_RENAME_FLAG = "--rename-dir"
_MAX_WAIT_SECONDS = 60


def _pid_alive(pid: int) -> bool:
    """可靠地判断进程是否存活

    Windows 下 os.kill(pid, 0) 对已退出进程的判定不可靠（可能误判存活，
    导致助手空等满 _MAX_WAIT_SECONDS）。改用 OpenProcess + GetExitCodeProcess；
    POSIX 下 os.kill(pid, 0) 足够可靠。
    """
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _prepend_launcher(args: list[str]) -> list[str]:
    """构造应用启动命令前缀（frozen 用 exe，源码用 python+main.py）"""
    if getattr(sys, "frozen", False):
        return [sys.executable] + args
    return [sys.executable, os.path.abspath(sys.argv[0])] + args


def spawn_relaunch(app_args: list[str]) -> None:
    """派发分离的助手进程，等待本进程退出后以 app_args 重新拉起应用"""
    helper = _prepend_launcher([_RELAUNCH_FLAG, str(os.getpid())] + app_args)
    subprocess.Popen(
        helper,
        close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def run_helper_if_requested() -> bool:
    """main() 入口最早调用：命中助手模式则等待并重启，返回 True（调用方应退出）。

    支持在重启前执行实例目录改名：--rename-dir <old_name> <new_name> 在旧进程
    退出（文件锁释放）后执行，避免 Windows 因打开的文件句柄而拒绝目录改名。
    """
    args = sys.argv[1:]
    if _RELAUNCH_FLAG not in args:
        return False

    idx = args.index(_RELAUNCH_FLAG)
    pid = int(args[idx + 1])
    rest = args[:idx] + args[idx + 2 :]

    rename_pair: tuple[str, str] | None = None
    if _RENAME_FLAG in rest:
        ri = rest.index(_RENAME_FLAG)
        rename_pair = (rest[ri + 1], rest[ri + 2])
        rest = rest[:ri] + rest[ri + 3 :]

    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.5)

    if rename_pair is not None:
        try:
            from bot.instances import rename_instance

            rename_instance(rename_pair[0], rename_pair[1])
        except Exception:
            # 改名失败不阻塞启动：目标实例可能保持旧名，交由新进程自行报错
            import logging

            logging.getLogger("qingci-bot.relaunch").exception(
                "跨进程改名失败: %r -> %r",
                rename_pair[0],
                rename_pair[1],
            )

    if rest:
        subprocess.Popen(
            _prepend_launcher(rest),
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    return True
