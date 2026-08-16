"""外部插件第三方依赖的按实例隔离安装与 sys.path 注入

设计（方案 A：依赖随插件分发，不随 exe 打包）：

- 依赖装入实例可写数据根下的 deps 目录（data_root()/deps/），遵循实例隔离；
- 加载外部插件前把该目录注入 sys.path，插件即可 import 其第三方库；
- 插件目录带 requirements.txt（或 plugin.json 的 requirements 字段）时，
  内容未变则跳过安装（幂等），变更才触发 pip 重装；
- pip 调用：源码模式走子进程 `python -m pip`；打包模式（frozen）走内嵌 pip
  （pip._internal），因此 exe 需随产物打包 pip。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger("qingci-bot.plugin.deps")


def deps_dir() -> Path:
    """返回实例隔离的插件依赖目录（自动创建）"""
    from ..paths import data_root

    d = data_root() / "deps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_in_sys_path() -> None:
    """把 deps 目录加入 sys.path（幂等），供插件 import 其第三方依赖"""
    d = deps_dir()
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))


def read_requirements(directory: Path) -> list[str]:
    """读取插件的依赖声明：requirements.txt 优先，其次 plugin.json 的 requirements 字段"""
    req_file = directory / "requirements.txt"
    if req_file.is_file():
        lines = req_file.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    meta_file = directory / "plugin.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        reqs = meta.get("requirements")
        if isinstance(reqs, list):
            return [str(r) for r in reqs]
    return []


def _hash_requirements(reqs: list[str]) -> str:
    h = hashlib.sha256()
    for r in reqs:
        h.update(r.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _marker_path(name: str) -> Path:
    return deps_dir() / ".installed" / f"{name}.hash"


async def ensure_dependencies(directory: Path) -> list[str]:
    """确保插件目录声明的第三方依赖已安装到实例 deps 目录

    返回声明的依赖列表；安装失败时记录警告但不抛异常（插件加载时
    若确实缺依赖会以 ImportError 呈现，便于重试）。
    """
    reqs = read_requirements(directory)
    ensure_in_sys_path()
    if not reqs:
        return []

    want = _hash_requirements(reqs)
    marker = _marker_path(directory.name)
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == want:
        return reqs

    ok = await _install(reqs)
    if ok:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(want, encoding="utf-8")
    else:
        logger.warning(f"插件 {directory.name} 依赖安装失败，插件可能无法正常工作")
    return reqs


async def _install(reqs: list[str]) -> bool:
    """把依赖安装到 deps 目录（按运行模式选择 pip 方式）"""
    target = deps_dir()
    if getattr(sys, "frozen", False):
        return _install_in_process(target, reqs)
    return await _install_subprocess(target, reqs)


def _pip_args(target: Path, reqs: list[str]) -> list[str]:
    return [
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--target",
        str(target),
        *reqs,
    ]


async def _install_subprocess(target: Path, reqs: list[str]) -> bool:
    """源码模式安装到 target：优先 uv，其次 python -m pip

    Qingci 的 uv 管理 venv 默认不带 pip，故优先用 uv pip install --target；
    非 uv 环境回退到 python -m pip。
    """
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--target", str(target), *reqs]
    else:
        cmd = [sys.executable, "-m", "pip", *_pip_args(target, reqs)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"依赖安装失败: {stdout.decode(errors='replace')[-2000:]}")
            return False
        return True
    except (OSError, ValueError) as e:
        logger.error(f"无法运行依赖安装器: {e}")
        return False


def _install_in_process(target: Path, reqs: list[str]) -> bool:
    """打包模式：调用内嵌 pip（pip._internal.main）安装到 target"""
    try:
        from pip._internal.cli.main import main as pip_main
    except ImportError:
        logger.error(
            "打包模式下未内置 pip，无法自动安装插件依赖；"
            "请在源码环境安装，或手动将依赖装入实例 deps 目录"
        )
        return False
    try:
        code = pip_main(_pip_args(target, reqs))
        return bool(code == 0)
    except SystemExit as e:
        logger.error(f"pip 安装失败: {e}")
        return False
