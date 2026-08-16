"""应用根目录解析（兼容 PyInstaller frozen 模式）

- 源码模式：项目根目录（bot/ 的上级目录）
- frozen 模式（PyInstaller onedir）：exe 所在目录。
  可写资源（config.yaml、data/）与静态资源（web/dist）随 exe 目录分发，
  不能用 __file__（frozen 时指向 _internal 内部）定位。
"""

import sys
from pathlib import Path

# 可写数据根目录的运行时覆盖（默认 app_root()/data）。
# 通过 --data-dir 设置，实现多进程多实例下各实例数据（DB/插件数据/日志等）相互隔离。
_data_root: Path | None = None


def app_root() -> Path:
    """返回应用根目录（绝对路径）"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def set_data_root(path: str | Path) -> None:
    """设置可写数据根目录（应尽早于任何数据访问前调用）"""
    global _data_root
    _data_root = Path(path).resolve()


def data_root() -> Path:
    """返回可写数据根目录（默认 app_root()/data）"""
    if _data_root is not None:
        return _data_root
    return app_root() / "data"
