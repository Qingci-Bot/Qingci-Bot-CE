"""应用根目录解析（兼容 PyInstaller frozen 模式）

- 源码模式：项目根目录（bot/ 的上级目录）
- frozen 模式（PyInstaller onedir）：exe 所在目录。
  可写资源（config.yaml、data/）与静态资源（web/dist）随 exe 目录分发，
  不能用 __file__（frozen 时指向 _internal 内部）定位。
"""

import sys
from pathlib import Path


def app_root() -> Path:
    """返回应用根目录（绝对路径）"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
