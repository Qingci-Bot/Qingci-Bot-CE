"""版本号统一升级工具

避免升级版本号时逐文件手动同步、且容易漏改。该脚本从一个版本号
同步更新以下文件（任一丢失即报错，防止静默漏改）：

    - pyproject.toml         [project].version（打包与 CI 读取）
    - bot/__init__.py        __version__（后端 /api/bot/status 返回，
                             关于页据此动态显示，无需单独改）
    - web/package.json       version（前端构建元数据）
    - web/package-lock.json  version（顶层 + packages[""] 两处根项目版本，
                             依赖包自身版本不属项目版本、保持不变）

用法：
    python scripts/bump_version.py 1.7.0     # 同步升级各文件版本号
    python scripts/bump_version.py --check   # 校验各文件版本号是否一致（Commit 前自查用）

退出码：成功 0；任一文件缺失/版本格式非法/升级失败/校验不一致时为 1。
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PACKAGE_INIT = PROJECT_ROOT / "bot" / "__init__.py"
WEB_PACKAGE_JSON = PROJECT_ROOT / "web" / "package.json"
WEB_PACKAGE_LOCK = PROJECT_ROOT / "web" / "package-lock.json"

# 语义化版本：主.次.补丁，可带 -预发布 或 +构建元数据 后缀（符合 PEP 440 常见形态）
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+_-]+)?$")

# 各模式均以具名分组 v 捕获版本号本体
_PROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"(?P<v>[^"]+)"', re.MULTILINE)
_INIT_VERSION_RE = re.compile(r'^__version__\s*=\s*"(?P<v>[^"]+)"', re.MULTILINE)
_PACKAGE_JSON_VERSION_RE = re.compile(r'^[ \t]*"version"\s*:\s*"(?P<v>[^"]+)"', re.MULTILINE)


def _read_value(content: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(content)
    return match.group("v") if match else None


def _replace_value(content: str, pattern: re.Pattern[str], new_version: str) -> str | None:
    """将首个匹配的版本号替换为 new_version；未匹配返回 None"""
    match = pattern.search(content)
    if not match:
        return None
    start, end = match.span("v")
    return content[:start] + new_version + content[end:]


# package-lock.json 中代表根项目版本的两处：顶层 version 与 packages[""].version
# （依赖包自身的 version 字段不属于项目版本，不得改动）
_LOCK_TOP_VERSION_RE = re.compile(r'^  "version": "(?P<v>[^"]+)"', re.MULTILINE)
_LOCK_PACKAGE_VERSION_RE = re.compile(r'^        "version": "(?P<v>[^"]+)"', re.MULTILINE)


def update_package_lock_version(content: str, new_version: str) -> str | None:
    """同步 package-lock.json 中根项目的两处版本号；任一缺失返回 None"""
    for pattern in (_LOCK_TOP_VERSION_RE, _LOCK_PACKAGE_VERSION_RE):
        updated = _replace_value(content, pattern, new_version)
        if updated is None:
            return None
        content = updated
    return content


def _project_range(content: str) -> tuple[int, int] | None:
    """pyproject.toml 中 [project] 节的起止偏移（start 处为该节首行，end 处为下一节首行）"""
    lines = content.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == "[project]"), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            break
    return start, end


def read_pyproject_version(content: str) -> str | None:
    prange = _project_range(content)
    if prange is None:
        return None
    start, end = prange
    return _read_value("".join(content.splitlines(keepends=True)[start:end]), _PROJECT_VERSION_RE)


def write_pyproject_version(content: str, new_version: str) -> str | None:
    prange = _project_range(content)
    if prange is None:
        return None
    start, end = prange
    lines = content.splitlines(keepends=True)
    prefix = "".join(lines[:start])
    block = "".join(lines[start:end])
    suffix = "".join(lines[end:])
    replaced = _replace_value(block, _PROJECT_VERSION_RE, new_version)
    if replaced is None:
        return None
    return prefix + replaced + suffix


def read_versions() -> dict[str, str | None]:
    lock_content = WEB_PACKAGE_LOCK.read_text(encoding="utf-8")
    return {
        "pyproject.toml": read_pyproject_version(PYPROJECT.read_text(encoding="utf-8")),
        "bot/__init__.py": _read_value(PACKAGE_INIT.read_text(encoding="utf-8"), _INIT_VERSION_RE),
        "web/package.json": _read_value(
            WEB_PACKAGE_JSON.read_text(encoding="utf-8"), _PACKAGE_JSON_VERSION_RE
        ),
        "web/package-lock.json": _read_value(lock_content, _LOCK_TOP_VERSION_RE),
        "web/package-lock.json[packages]": _read_value(lock_content, _LOCK_PACKAGE_VERSION_RE),
    }


def apply_version(new_version: str) -> bool:
    """同步升级各文件版本号，全部成功返回 True"""
    pyproject = write_pyproject_version(PYPROJECT.read_text(encoding="utf-8"), new_version)
    if pyproject is None:
        print(f"[错误] {_display(PYPROJECT)} 缺少 [project].version")
        return False
    PYPROJECT.write_text(pyproject, encoding="utf-8")

    ok = True
    for path, pattern in (
        (PACKAGE_INIT, _INIT_VERSION_RE),
        (WEB_PACKAGE_JSON, _PACKAGE_JSON_VERSION_RE),
    ):
        content = path.read_text(encoding="utf-8")
        updated = _replace_value(content, pattern, new_version)
        if updated is None:
            print(f"[错误] {_display(path)} 中未找到版本号字段")
            ok = False
        else:
            path.write_text(updated, encoding="utf-8")

    lock_updated = update_package_lock_version(
        WEB_PACKAGE_LOCK.read_text(encoding="utf-8"), new_version
    )
    if lock_updated is None:
        print(f"[错误] {_display(WEB_PACKAGE_LOCK)} 中未找到根项目版本号字段")
        ok = False
    else:
        WEB_PACKAGE_LOCK.write_text(lock_updated, encoding="utf-8")
    return ok


def _display(path: Path) -> Path:
    """返回相对项目根目录的路径展示；根目录外 path 原样返回（避免 relative_to 抛错）"""
    try:
        return path.relative_to(PROJECT_ROOT)
    except ValueError:
        return path


def main() -> int:
    parser = argparse.ArgumentParser(description="统一升级/校验 Qingci-Bot CE 版本号")
    parser.add_argument("version", nargs="?", help="新版本号，如 1.7.0")
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅校验三处版本号是否一致，不修改文件",
    )
    args = parser.parse_args()

    if not args.version and not args.check:
        parser.error("需要提供新版本号，或使用 --check 仅校验一致性")

    if args.version and not SEMVER_RE.match(args.version):
        print(f"[错误] 非法版本号: {args.version!r}（需符合 主.次.补丁 格式）")
        return 1

    versions = read_versions()
    distinct = {v for v in versions.values() if v}
    if len(distinct) > 1:
        print("[不一致] 版本号不统一：")
        for name, ver in versions.items():
            print(f"  {name}: {ver or '(未找到)'}")
        return 1

    if args.check:
        print(f"[一致] 全部版本号均为: {next(iter(distinct), '(未找到)')}")
        return 0

    assert args.version is not None
    if not apply_version(args.version):
        return 1
    print(f"[完成] 已将全部版本号统一升级为 {args.version}:")
    for name in (
        "pyproject.toml",
        "bot/__init__.py",
        "web/package.json",
        "web/package-lock.json",
        "web/package-lock.json[packages]",
    ):
        print(f"  {name} ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
