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

from ._proc import NO_WINDOW_FLAG

logger = logging.getLogger("qingci-bot.plugin.deps")

# 依赖安装子进程最大耗时（秒）：超时 kill 并报错，避免挂死事件循环
_INSTALL_TIMEOUT = 300


def deps_root() -> Path:
    """返回实例隔离的插件依赖根目录（自动创建）

    依赖按插件隔离到 `deps/<plugin_name>/` 子目录，避免跨插件同名不同版本
    互相覆盖、以及某插件声明的包遮蔽框架内置包；`deps_root()` 本身仅承载
    安装标记（`.installed/`）。
    """
    from ..paths import data_root

    d = data_root() / "deps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def deps_dir(name: str) -> Path:
    """返回指定插件的专属依赖目录（`deps/<name>/`，自动创建）"""
    d = deps_root() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_in_sys_path(name: str) -> None:
    """把指定插件的依赖目录加入 sys.path（幂等），供该插件 import 其第三方依赖"""
    d = deps_dir(name)
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


def _sanitize_requirements(reqs: list[str]) -> list[str]:
    """过滤依赖项中的 pip 选项注入（如 --index-url / -e / -r 等）

    插件 requirements.txt / plugin.json 的每一行应是包名约束（如
    qingci-plugin-sdk>=1.0）；拒绝以 ``-`` 开头的项，防止恶意插件注入
    任意 pip 命令行选项（覆盖索引源、指定本地路径安装等供给链攻击面）。
    """
    clean: list[str] = []
    for r in reqs:
        item = r.strip()
        if not item:
            continue
        if item.startswith("-"):
            logger.warning(f"忽略非依赖行（疑似 pip 选项注入）: {item!r}")
            continue
        clean.append(item)
    return clean


def _hash_requirements(reqs: list[str]) -> str:
    h = hashlib.sha256()
    for r in reqs:
        h.update(r.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _marker_path(name: str) -> Path:
    return deps_root() / ".installed" / f"{name}.hash"


async def ensure_dependencies(directory: Path) -> list[str]:
    """确保插件目录声明的第三方依赖已安装到该插件独立的 deps 子目录

    返回声明的依赖列表；安装失败时记录警告但不抛异常（插件加载时
    若确实缺依赖会以 ImportError 呈现，便于重试）。
    """
    reqs = read_requirements(directory)
    if not reqs:
        return []
    name = directory.name

    # 过滤 pip 选项注入后，声明与安装使用同一份净化列表
    reqs = _sanitize_requirements(reqs)
    want = _hash_requirements(reqs)
    marker = _marker_path(name)
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == want:
        ensure_in_sys_path(name)
        return reqs

    ok = await _install(reqs, deps_dir(name))
    if ok:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(want, encoding="utf-8")
    else:
        logger.warning(f"插件 {name} 依赖安装失败，插件可能无法正常工作")
    # 无论成败都注入 sys.path：失败时用户手动补装依赖后立即生效
    ensure_in_sys_path(name)
    return reqs


async def _install(reqs: list[str], target: Path) -> bool:
    """把依赖安装到指定插件的 deps 子目录（按运行模式选择安装器）"""
    if getattr(sys, "frozen", False):
        return await _install_frozen(target, reqs)
    # 源码模式：优先 uv（Qingci 的 uv venv 默认不带 pip），其次 python -m pip
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--target", str(target), *reqs]
        return await _run_installer(cmd)
    return await _install_subprocess(target, reqs)


async def _install_frozen(target: Path, reqs: list[str]) -> bool:
    """打包模式：优先内置 uv 子进程，其次内嵌 pip"""
    uv = _bundled_uv()
    if uv is not None:
        cmd = [uv, "pip", "install", "--target", str(target), *reqs]
        return await _run_installer(cmd)
    return _install_in_process(target, reqs)


def _bundled_uv() -> str | None:
    """打包模式定位随产物的 uv 可执行文件；未打包则返回 None"""
    meipass = getattr(sys, "_MEIPASS", None)  # PyInstaller 专用，非打包环境不存在
    if meipass:
        uv = str(Path(meipass, "uv.exe"))
        if shutil.which(uv):
            return uv
    return None


def _pip_args(target: Path, reqs: list[str]) -> list[str]:
    return [
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--target",
        str(target),
        *reqs,
    ]


async def _run_installer(cmd: list[str]) -> bool:
    """以子进程方式运行安装命令（uv 或 python -m pip），含超时回收"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=NO_WINDOW_FLAG,
        )
    except (OSError, ValueError) as e:
        logger.error(f"无法运行依赖安装器: {e}")
        return False
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_INSTALL_TIMEOUT)
    except asyncio.TimeoutError:
        # communicate 超时：进程可能卡死，终止并回收，避免僵尸进程
        try:
            proc.kill()
            await proc.wait()
        except (OSError, ProcessLookupError):
            pass
        logger.error(f"依赖安装超时（>{_INSTALL_TIMEOUT}s），已终止安装进程")
        return False
    if proc.returncode != 0:
        logger.error(f"依赖安装失败: {stdout.decode(errors='replace')[-2000:]}")
        return False
    return True


async def _install_subprocess(target: Path, reqs: list[str]) -> bool:
    """源码模式：python -m pip 安装到 target（uv 分支已在 _install 中处理）"""
    cmd = [sys.executable, "-m", "pip", *_pip_args(target, reqs)]
    return await _run_installer(cmd)


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
