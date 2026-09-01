"""应用根目录解析（兼容 PyInstaller frozen 模式）

- 源码模式：项目根目录（bot/ 的上级目录）
- frozen 模式（PyInstaller onedir）：exe 所在目录。
  可写资源（config.yaml、data/）与静态资源（web/dist）随 exe 目录分发，
  不能用 __file__（frozen 时指向 _internal 内部）定位。
"""

import os
import sys
from pathlib import Path

# 可写数据根目录的运行时覆盖（默认 app_root()/data）。
# 通过 --data-dir / --instance 设置，实现多进程多实例下各实例数据（DB/插件数据/日志等）相互隔离。
_data_root: Path | None = None

# 外部插件代码目录的运行时覆盖（默认 app_root()/plugins）。
# 通过 --instance 设置，使实例拥有专属插件目录（完全自包含）。
_plugins_root: Path | None = None

# 当前进程是否运行桌面 UI（frozen windowed 下双击无参数时无法从 sys.argv 得知，
# 故在 main() 显式记录，供"切换实例"重建启动命令时保留 --desktop）。
_desktop: bool = False


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


def set_plugins_dir(path: str | Path) -> None:
    """设置外部插件代码目录（应尽早于任何插件加载前调用）"""
    global _plugins_root
    _plugins_root = Path(path).resolve()


def plugins_dir() -> Path:
    """返回外部插件代码目录（默认 app_root()/plugins）"""
    if _plugins_root is not None:
        return _plugins_root
    return app_root() / "plugins"


def instances_dir() -> Path:
    """返回实例注册表根目录（默认 app_root()/instances，每个实例一个子目录）

    源码/onedir 直跑：实例随 app_root()/instances 自包含。
    桌面壳（安装版/绿色解压版）形态：后端 onedir 被 electron-builder 复制进
    resources/backend/（frozen 时 app_root() 指向后端目录），Electron 壳经环境
    变量 QINGCI_APP_DIR 指向桌面 EXE 所在目录，实例统一落在桌面 EXE 旁的
    instances/ 下，便于整体拷贝迁移。
    """
    app_dir = os.environ.get("QINGCI_APP_DIR")
    if app_dir:
        return Path(app_dir).resolve() / "instances"
    return app_root() / "instances"


def set_desktop_flag(value: bool) -> None:
    """记录当前进程是否运行桌面 UI（供切换实例时重建启动命令）"""
    global _desktop
    _desktop = bool(value)


def is_desktop() -> bool:
    """当前进程是否运行桌面 UI"""
    return _desktop
