"""单实例保护 — 防止重复双击启动多个界面/进程

Windows 下基于命名互斥量（CreateMutexW）判断本进程是否为第一个实例：
- 首次实例：持有互斥句柄直到进程退出（否则系统会回收互斥，失去保护）
- 重复实例：释放句柄并返回 False，由调用方决定聚焦已有窗口后退出

仅在 Windows 下启用；非 Windows 环境无法可靠做跨进程互斥，放开不阻塞启动。

> 与 `bot/instances.py` 的「实例」区分：本模块指**进程级单例互斥**
> （同数据目录重复启动只留一个进程）；`bot/instances.py` 指**数据级多实例**
> （instances/<name>/ 自包含目录，不同 --data-dir 可并行多开）。
"""

import ctypes
import hashlib
import logging
import sys
from pathlib import Path

logger = logging.getLogger("qingci-bot.desktop.single_instance")

# kernel32 ERROR_ALREADY_EXISTS
_ERROR_ALREADY_EXISTS = 183
# user32 SW_RESTORE
_SW_RESTORE = 9

# 桌面主窗口标题（与 desktop/main.py 的 webview 窗口标题保持一致）
WINDOW_TITLE = "Qingci-Bot CE"

# 默认互斥名（后续打包/多配置场景可复用模块并传入不同 name）
DEFAULT_MUTEX_NAME = "Qingci-Bot-CE"


def mutex_name_for_data_dir(data_dir: str | Path) -> str:
    """由数据根目录派生互斥名：不同实例（不同数据根）互不阻塞，同一实例仍防重复"""
    key = str(Path(data_dir).resolve()).lower()
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"Qingci-Bot-CE-{digest}"


class SingleInstance:
    """进程级单实例互斥锁

    用法：
        inst = SingleInstance()
        if not inst.acquire():
            bring_existing_to_front()
            return
    """

    def __init__(self, name: str = DEFAULT_MUTEX_NAME):
        self._name = name
        self._handle = None

    def acquire(self) -> bool:
        """尝试获得唯一互斥。返回 True 表示本进程是第一个实例。"""
        if sys.platform != "win32":
            return True
        try:
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, self._name)
        except Exception:
            logger.warning("创建单实例互斥失败，跳过单实例保护", exc_info=True)
            return True
        if not handle:
            logger.warning("单实例互斥句柄创建为空，跳过单实例保护")
            return True
        already_exists = ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS
        if already_exists:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        # 首次实例：持有句柄直到进程退出，防止互斥被系统回收后失去保护
        self._handle = handle
        return True

    def release(self) -> None:
        """显式释放互斥（进程退出时系统会自动回收，通常无需手动调用）"""
        if self._handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


def bring_existing_to_front(title: str = WINDOW_TITLE) -> None:
    """将已运行的桌面窗口恢复并置前（若窗口存在）"""
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]  # Windows-only API
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, _SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
    except Exception:
        logger.warning("聚焦已有窗口失败", exc_info=True)
