# Copyright (C) 2026 Zhou Zhe (aka luoqingci) and Qingci-Bot contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

"""源码版权 / 许可证声明的检查与批量补齐

LICENSE 的 GPLv3 第 7 条附加条款 (a) 要求保留源码中的版权与作者声明，
因此每个源码文件都要有 Copyright + SPDX 标识。本脚本是这件事的单一来源：

    python scripts/license_headers.py --check   # 列出缺失声明的文件，退出码 1
    python scripts/license_headers.py --fix     # 幂等补齐（保留 BOM / 换行符 / shebang）

``--check`` 由 ``tests/test_license_headers.py`` 在 CI 里调用，新增源码文件
漏写声明会直接失败。CE 的 LICENSE 含第 7 条附加条款时，文件头会多一行
指向 LICENSE；Plugins-SDK 的纯 GPLv3 LICENSE 则不加该行（同一份脚本自动判断）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSE = ROOT / "LICENSE"

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".workbuddy",
    ".workbuddy-ai",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
}
SOURCE_SUFFIXES = {".py", ".js", ".vue", ".css", ".spec"}

COPYRIGHT = "Copyright (C) 2026 Zhou Zhe (aka luoqingci) and Qingci-Bot contributors"
SPDX = "SPDX-License-Identifier: GPL-3.0-or-later"
ADDITIONAL = "Additional terms under GPLv3 Section 7: see LICENSE"
SECTION7_MARKER = "ADDITIONAL TERMS UNDER GNU GPLv3 SECTION 7"
COPYRIGHT_MARKER = "Copyright (C) 2026"
SPDX_MARKER = "SPDX-License-Identifier:"


def iter_sources():
    """按稳定顺序产出需要声明的源码文件"""
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        yield path


def header_lines(path: Path) -> list[str]:
    """按扩展名生成注释块（Python/JS 用行注释，Vue/CSS 用块注释）"""
    lines = [COPYRIGHT, SPDX]
    if SECTION7_MARKER in LICENSE.read_text(encoding="utf-8", errors="ignore"):
        lines.append(ADDITIONAL)
    suffix = path.suffix
    if suffix in (".py", ".spec"):
        return [f"# {line}" for line in lines]
    if suffix == ".js":
        return [f"// {line}" for line in lines]
    if suffix == ".vue":
        return ["<!--", *(f"  {line}" for line in lines), "-->"]
    if suffix == ".css":
        return ["/*", *(f" * {line}" for line in lines), " */"]
    raise ValueError(f"未支持的扩展名: {suffix}")


def has_header(path: Path) -> bool:
    head = path.read_bytes()[:800].decode("utf-8", errors="ignore")
    return COPYRIGHT_MARKER in head and SPDX_MARKER in head


def fix_one(path: Path) -> None:
    """在文件开头插入声明；只改开头，不触碰其余内容与换行风格"""
    data = path.read_bytes()
    bom = b""
    if data.startswith(b"\xef\xbb\xbf"):
        bom, data = data[:3], data[3:]
    newline = b"\r\n" if b"\r\n" in data[:2000] else b"\n"
    block = newline.join(line.encode("utf-8") for line in header_lines(path)) + newline
    # 原文件为空时不再追加分隔空行，避免在 EOF 留下多余空行（git diff --check 会拦）
    if data.strip():
        block += newline

    # shebang 与 PEP 263 编码/模式行必须保持在首行（spec 文件有 coding 行）
    first_line = data.split(b"\n", 1)[0].lower()
    keep_first = data.startswith(b"#!") or (first_line.startswith(b"#") and b"coding" in first_line)
    if keep_first:
        first, _, rest = data.partition(b"\n")
        updated = bom + first + b"\n" + block + rest
    else:
        updated = bom + block + data
    path.write_bytes(updated)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="只检查，不修改文件")
    group.add_argument("--fix", action="store_true", help="补齐缺失的声明")
    args = parser.parse_args()

    sources = list(iter_sources())
    if args.check:
        missing = [p.relative_to(ROOT) for p in sources if not has_header(p)]
        if missing:
            print(f"[缺失] {len(missing)} 个源码文件没有版权/SPDX 声明:")
            for path in missing:
                print(f"  {path}")
            print("请运行: python scripts/license_headers.py --fix")
            return 1
        print(f"[一致] 全部 {len(sources)} 个源码文件均带版权/SPDX 声明")
        return 0

    changed = 0
    for path in sources:
        if has_header(path):
            continue
        fix_one(path)
        changed += 1
    print(f"[完成] 已为 {changed} 个源码文件补齐声明（共扫描 {len(sources)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
