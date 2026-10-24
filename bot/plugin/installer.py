"""插件安装/卸载 — 来源拉取、归档安全解压、目录落位

从 ``PluginManager`` 拆出的独立职责单元：管理器只保留「插件生命周期与调度」，
安装链路的 IO/安全校验细节不再混在 1700 行的单文件里。

安全边界（本模块是唯一入口，改动需保持这些约束）：
- 远程来源仅允许 http(s) / git，且经 ``ssrf`` 校验协议白名单与私网地址
- 归档下载有大小上限，解压有总量上限（防 zip 炸弹）
- 解压前预检每个成员路径，拒绝绝对路径、``..`` 穿越与符号链接（防 Zip Slip）
- 插件名经正则校验，拒绝路径穿越；落位先写 staging 再原子替换

对管理器的依赖只有 ``load_external`` 与 ``unload`` 两个方法。
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._proc import NO_WINDOW_FLAG
from .deps import ensure_dependencies

if TYPE_CHECKING:
    from .manager import PluginManager

logger = logging.getLogger("qingci-bot.plugin.installer")

# 插件名合法性：仅允许字母/数字/下划线/连字符，禁止路径穿越（../ 等）
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")

# 归档下载/解压大小上限（防 zip 炸弹撑爆磁盘）
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024  # 下载上限 200MB
_MAX_EXTRACT_BYTES = 1024 * 1024 * 1024  # 解压总量上限 1GB


def is_valid_plugin_name(name: str) -> bool:
    """校验插件名是否合法（用于市场安装等从外部输入获取插件名的场景）"""
    return bool(_PLUGIN_NAME_RE.fullmatch(name))


def sha256_file(path: Path) -> str:
    """计算文件 SHA256（分块读取，防止归档大文件一次读入内存）"""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff_archive_format(path: Path) -> str | None:
    """按文件头嗅探归档格式：zip / tar / 其他（扩展名缺失时的兜底）"""
    import tarfile
    import zipfile

    try:
        if zipfile.is_zipfile(path):
            return "zip"
        if tarfile.is_tarfile(path):
            return "tar"
    except OSError:
        return None
    return None


class PluginInstaller:
    """插件安装器（由 PluginManager 持有，对外经 manager.install / manager.remove 调用）"""

    def __init__(self, manager: PluginManager):
        self._manager = manager

    # ============ 安装 ============

    async def install(
        self,
        bot: Any,
        source: str,
        *,
        name: str | None = None,
        allow_local: bool = False,
        expected_sha256: str = "",
    ) -> bool:
        """在线安装插件到外部插件目录并加载

        source 支持：
        - git 仓库地址（git+https://... / https://... / git@...）
        - HTTP 指向 zip/tar 归档的 URL
        - 本地路径（目录或归档文件，仅 allow_local=True 时接受）

        安装过程：拉取到 plugins/<name>/ → 加载 requirements.txt 依赖 → 加载插件。

        Args:
            bot: Bot 实例（用于加载）
            source: 插件来源
            name: 目标插件名（为空时从来源推断）
            allow_local: 是否接受本地路径来源。市场安装来源由远端索引下发，
                必须保持 False（禁止本地路径），防止恶意索引复制服务器任意目录。
            expected_sha256: 归档校验和（仅 HTTP 归档来源生效；git 自带完整性）
        """
        from ..paths import plugins_dir

        plugin_dir = plugins_dir()
        plugin_dir.mkdir(parents=True, exist_ok=True)

        target_name, target_dir = await self._fetch_plugin(
            source,
            name,
            plugin_dir,
            allow_local=allow_local,
            expected_sha256=expected_sha256,
        )
        if target_dir is None:
            return False

        # 自动安装依赖（requirements.txt / plugin.json 的 requirements 字段，
        # 装入该插件独立的 deps/<name>/ 子目录而非全局环境）
        await ensure_dependencies(target_dir)

        # 确保 plugins 父目录在 sys.path（与 load_external_dir 一致），
        # 避免 EXE 首次安装（目录新建、此前未扫描）时 import 失败
        root_str = str(plugin_dir.parent)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        # 加载插件
        module_path = f"plugins.{target_name}"
        try:
            return await self._manager.load_external(module_path, bot)
        except Exception:
            logger.exception(f"安装后加载插件失败: {module_path}")
            return False

    async def _fetch_plugin(
        self,
        source: str,
        name: str | None,
        plugins_dir: Path,
        allow_local: bool = False,
        expected_sha256: str = "",
    ) -> tuple[str, Path | None]:
        """拉取插件源码到 plugins/ 目录，返回 (插件名, 插件目录)"""
        import shutil
        import tempfile

        source = source.strip()
        is_local_path = Path(source).exists()
        temp_dir: str | None = None
        try:
            if is_local_path:
                if not allow_local:
                    logger.error(f"拒绝本地路径插件来源（远程安装仅允许 http(s)/git）: {source}")
                    return "", None
                src = Path(source).resolve()
                if src.is_dir():
                    return await self._copy_plugin_dir(src, name, plugins_dir)
                if src.is_file() and self._is_archive(src):
                    temp_dir = tempfile.mkdtemp(prefix="qb-plugin-")
                    extracted = await self._extract_archive(src, Path(temp_dir))
                    return await self._copy_plugin_dir(extracted, name, plugins_dir)
                logger.error(f"本地插件来源无效: {source}")
                return "", None
            else:
                # 远程来源：git 克隆或 HTTP 下载归档
                temp_dir = tempfile.mkdtemp(prefix="qb-plugin-")
                staging = Path(temp_dir) / "src"
                # git 仓库识别：git+ / git@ / ssh:// 前缀，或 .git 结尾的 http(s) URL
                is_git = source.startswith(("git+", "git@", "ssh://")) or (
                    source.startswith(("http://", "https://"))
                    and source.rstrip("/").endswith(".git")
                )
                if is_git:
                    from .ssrf import is_allowed_git_url, is_private_url

                    repo = source[4:] if source.startswith("git+") else source
                    # 与 _download_archive 同基线：git 来源同样校验协议白名单
                    # 与私网地址，防止恶意索引借 git+file:// 或内网 SSH 绕过防护
                    if not is_allowed_git_url(repo) or is_private_url(repo):
                        logger.error(f"拒绝不安全的 git 来源: {source}")
                        return "", None
                    ok = await self._run_subprocess(
                        ["git", "clone", "--depth", "1", repo, str(staging)]
                    )
                    if not ok or not staging.exists():
                        logger.error(f"git 克隆失败: {source}")
                        return "", None
                elif source.startswith(("http://", "https://")):
                    ok = await self._download_archive(source, staging)
                    if not ok:
                        logger.error(f"下载归档失败: {source}")
                        return "", None
                    # 归档完整性校验：市场索引声明 source_sha256 时校验下载内容，
                    # 防传输被篡改/投毒（git 来源跳过，git 自带内容完整性）
                    if expected_sha256:
                        actual = await asyncio.to_thread(sha256_file, staging)
                        if actual != expected_sha256:
                            logger.error(f"插件归档校验失败（sha256 不匹配，可能被篡改）: {source}")
                            return "", None
                    staging = await self._extract_archive(staging, staging.parent)
                else:
                    logger.error(f"不支持的插件来源: {source}")
                    return "", None

                # 找到插件目录（仓库根或其下含 plugin.json/__init__.py 的目录；
                # 指定 name 时优先精确匹配，兼容 plugins/<name>/ 嵌套布局）
                plugin_dir = self._locate_plugin_dir(staging, name)
                if plugin_dir is None:
                    logger.error(f"来源中未找到插件目录: {source}")
                    return "", None
                return await self._copy_plugin_dir(plugin_dir, name, plugins_dir)
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def _copy_plugin_dir(
        self, src: Path, name: str | None, plugins_dir: Path
    ) -> tuple[str, Path | None]:
        """复制插件目录到 plugins/，返回 (插件名, 目标目录)

        先复制到 staging 目录再原子替换，失败时保留旧版本插件目录。
        """
        import shutil
        import uuid

        target_name = name or src.name
        # 插件名合法性校验：市场索引/仓库目录名可能被篡改（路径穿越）
        if not is_valid_plugin_name(target_name):
            logger.error(f"非法插件名，拒绝安装: {target_name!r}")
            return "", None
        target = plugins_dir / target_name
        # 先复制到 staging，成功后原子替换（同目录 rename），失败保留旧版本
        staging = plugins_dir / f".staging-{target_name}-{uuid.uuid4().hex[:8]}"
        try:
            shutil.copytree(
                src, staging, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv")
            )
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
            return target_name, target
        except OSError as e:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            logger.error(f"复制插件目录失败: {e}")
            return "", None

    @staticmethod
    async def _run_subprocess(cmd: list[str]) -> bool:
        """运行子进程并返回是否成功（communicate 带超时，防止永久挂起）"""

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=NO_WINDOW_FLAG,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            except TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                logger.error(f"子进程超时（{cmd[0]}），已终止")
                return False
            if proc.returncode != 0:
                logger.error(f"子进程失败 ({cmd[0]}): {stdout.decode(errors='replace')[-2000:]}")
                return False
            return True
        except (OSError, ValueError) as e:
            logger.error(f"无法运行子进程 {cmd[0]}: {e}")
            return False

    @staticmethod
    async def _download_archive(url: str, dest: Path) -> bool:
        """下载远程归档文件到 dest（同步 urllib 放入线程池，避免阻塞事件循环）"""
        import urllib.request

        from .ssrf import NoRedirectHandler, is_private_url

        # 来源可能由远端市场索引下发：拦截私网/环回地址，防止 SSRF
        if is_private_url(url):
            logger.error(f"拒绝下载私网/环回地址的归档: {url}")
            return False

        def _sync_download() -> bool:
            try:
                # 禁止跟随重定向，防止 302 跳转到内网地址
                opener = urllib.request.build_opener(NoRedirectHandler)
                req = urllib.request.Request(url, headers={"User-Agent": "Qingci-Bot-CE"})
                with opener.open(req, timeout=60) as resp:
                    # 归档大小上限（防 zip 炸弹撑爆磁盘；Content-Length 缺失时边下边限）
                    declared = resp.headers.get("Content-Length")
                    if declared and int(declared) > _MAX_ARCHIVE_BYTES:
                        logger.error(f"归档声明过大，拒绝下载: {url} ({declared} bytes)")
                        return False
                    remaining = _MAX_ARCHIVE_BYTES
                    with open(dest, "wb") as f:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            if remaining < 0:
                                logger.error(
                                    f"归档超过大小上限（{_MAX_ARCHIVE_BYTES}），中止下载: {url}"
                                )
                                return False
                            f.write(chunk)
                return True
            except (OSError, urllib.error.URLError, ValueError) as e:
                logger.error(f"下载失败 {url}: {e}")
                return False

        return await asyncio.to_thread(_sync_download)

    @staticmethod
    async def _extract_archive(archive: Path, dest_dir: Path) -> Path:
        """解压 zip/tar 归档，返回解压后的根目录

        无法按扩展名判断格式时嗅探文件头（HTTP 下载的暂存文件通常无扩展名）。
        """
        import stat
        import tarfile
        import zipfile

        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = archive.name.lower()
        fmt = "zip" if fname.endswith(".zip") else None
        if fmt is None and fname.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
            fmt = "tar"
        if fmt is None:
            fmt = sniff_archive_format(archive)
        if fmt == "zip":
            with zipfile.ZipFile(archive) as zf:
                # 解压总量上限（防 zip 炸弹）
                if sum(i.file_size for i in zf.infolist()) > _MAX_EXTRACT_BYTES:
                    raise ValueError("归档解压后过大，已拒绝")
                # 安全解压：与下方 tar 分支同规则，预检每个成员路径，
                # 拒绝绝对路径（含盘符）与 .. 穿越条目，防止 Zip Slip；
                # 同时拒绝符号链接条目（防 symlink 逃逸覆写目录外文件）
                dest_root = dest_dir.resolve()
                for info in zf.infolist():
                    member = info.filename.replace("\\", "/")
                    member_target = (dest_root / member).resolve()
                    if not member_target.is_relative_to(dest_root):
                        raise ValueError(f"归档包含非法路径: {info.filename}")
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise ValueError(f"归档包含符号链接，已拒绝: {info.filename}")
                zf.extractall(dest_dir)
        elif fmt == "tar":
            with tarfile.open(archive) as tf:
                # 解压总量上限（防 zip 炸弹）
                if sum(m.size for m in tf.getmembers()) > _MAX_EXTRACT_BYTES:
                    raise ValueError("归档解压后过大，已拒绝")
                # 安全解压：Py3.12+ 用 data_filter；旧版本手动预检成员路径，
                # 拒绝绝对路径与 .. 成员，并拒绝 symlink/hardlink 成员——
                # 3.10/3.11 下预检只查文本路径，symlink 可指向解压目录外
                # 再经后续成员写入实现逃逸（Zip Slip 变体）
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(dest_dir, filter="data")
                else:
                    dest_root = dest_dir.resolve()
                    for m in tf.getmembers():
                        if m.issym() or m.islnk():
                            raise ValueError(f"归档包含符号链接，已拒绝: {m.name}")
                        member_target = (dest_root / m.name).resolve()
                        if not member_target.is_relative_to(dest_root):
                            raise ValueError(f"归档包含非法路径: {m.name}")
                    tf.extractall(dest_dir)
        else:
            raise ValueError(f"不支持的归档格式: {fname}")
        # 若顶层为单个目录，返回该目录
        entries = [p for p in dest_dir.iterdir() if not p.name.startswith("__")]
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return dest_dir

    @staticmethod
    def _locate_plugin_dir(root: Path, name: str | None = None) -> Path | None:
        """定位仓库/归档中的插件目录

        1. 根目录本身是插件（含 plugin.json 或 __init__.py）→ 返回根
        2. 指定 name 时：递归（最多 2 层）查找目录名 == name 且是插件的目录
        3. 否则：返回首个直接子目录中的插件
        4. 直接子目录中找不到时，再向下找一层（兼容 plugins/<name>/ 嵌套）
        """
        if (root / "plugin.json").is_file() or (root / "__init__.py").is_file():
            return root

        def _is_plugin_dir(d: Path) -> bool:
            return (d / "plugin.json").is_file() or (d / "__init__.py").is_file()

        if name:
            for pattern in (f"*/{name}", f"*/*/{name}"):
                for child in root.glob(pattern):
                    if child.is_dir() and _is_plugin_dir(child):
                        return child

        for child in sorted(root.iterdir()):
            if child.is_dir() and _is_plugin_dir(child):
                return child

        # 嵌套一层：兼容 plugins/<name>/ 布局
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            for sub in sorted(child.iterdir()):
                if sub.is_dir() and _is_plugin_dir(sub):
                    return sub
        return None

    @staticmethod
    def _is_archive(path: Path) -> bool:
        return path.suffix.lower() in (".zip", ".tar", ".gz", ".tgz", ".bz2")

    # ============ 卸载 ============

    async def remove(self, name: str, *, purge: bool = False) -> None:
        """卸载并删除插件

        purge=False（默认）：卸载 + 删除插件代码目录，保留数据与依赖
        （便于重装）。实例模式下代码目录与数据目录重合
        （instances/<inst>/plugins/<name>），删除代码目录会连带删除插件数据，
        为避免数据丢失，重合时默认仅卸载不删文件（P1 修复，见插件卸载评估报告）。
        purge=True：彻底删除——卸载 + 删除代码目录、独立数据目录
        （data_root()/plugins/<name>）与第三方依赖（deps/、安装标记、sys.path 注入）。
        """
        await self._manager.unload(name)
        from ..paths import data_root, plugins_dir

        base = plugins_dir()
        # 兜底校验：解析后必须位于插件目录内（防插件声明恶意 name 越界删除）
        plugin_dir = (base / name).resolve()
        if not plugin_dir.is_relative_to(base.resolve()):
            raise ValueError(f"非法插件删除路径: {name!r}")

        data_dir = (data_root() / "plugins" / name).resolve()
        # 实例模式下 plugins_dir 与 data_root()/plugins 重合 → 代码目录即数据目录
        code_and_data_merge = plugin_dir == data_dir

        import shutil

        def _rmtree(path: Path, label: str) -> None:
            if not path.exists():
                return
            if not path.is_dir():
                logger.warning(f"插件 {name} {label}路径非目录，跳过删除: {path}")
                return
            try:
                shutil.rmtree(path)
            except OSError as e:
                raise RuntimeError(f"插件 {name} {label}删除失败（文件可能被占用）: {path}") from e

        if purge:
            _rmtree(plugin_dir, "代码/数据目录")
            if not code_and_data_merge:
                _rmtree(data_dir, "数据目录")
            from .deps import cleanup_dependencies

            try:
                cleanup_dependencies(name)
            except Exception:
                logger.exception(f"清理插件 {name} 依赖异常")
            logger.info(f"插件已彻底删除: {name} (purge)")
            return

        if code_and_data_merge:
            # 实例模式：默认删除即删数据，仅卸载保留文件，彻底删除需 purge
            logger.info(
                f"插件 {name} 实例模式下代码与数据同目录，仅卸载不删文件"
                "（如需彻底删除请使用 purge）"
            )
            return
        _rmtree(plugin_dir, "代码目录")
        logger.info(f"插件已删除: {name} ({plugin_dir})")
