"""把 PyInstaller onedir 打成单个 zip，供桌面壳首次启动解压

为什么这么做：安装慢的主因是文件数。onedir 有数千个文件，NSIS 逐个落盘 +
杀毒软件逐个扫描，装一次要好几分钟。打成单个 zip 后，安装阶段只写 1 个大文件，
解压推迟到首次启动（可显示进度），且只发生一次。

用 ZIP_STORED（不压缩）：外层 NSIS 安装包本身会压缩，这里再压一遍没有收益，
反而让解压变慢。

用法：
    python scripts/pack_backend.py            # 默认 dist/qingci-bot-ce → dist/backend.zip
    python scripts/pack_backend.py --src X --out Y
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

DEFAULT_SRC = Path("dist/qingci-bot-ce")
DEFAULT_OUT = Path("dist/backend.zip")


def pack(src: Path, out: Path) -> tuple[int, int]:
    """返回 (文件数, 字节数)"""
    if not src.is_dir():
        raise SystemExit(f"[pack_backend] 源目录不存在：{src}")

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    files = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for root, _dirs, names in os.walk(src):
            for name in names:
                path = Path(root) / name
                # 归档内保持相对路径，解压后即为 onedir 的完整结构
                zf.write(path, path.relative_to(src).as_posix())
                files += 1
    return files, out.stat().st_size


def main() -> int:
    # Windows 控制台默认代码页不一定是 UTF-8（CI 上是 cp1252），
    # 直接 print 非 ASCII 会抛 UnicodeEncodeError 并中断打包
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="把后端 onedir 打成单个 zip")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    files, size = pack(args.src, args.out)
    print(f"[pack_backend] {args.src} → {args.out}")
    print(f"[pack_backend] {files} 个文件，{size / 1024 / 1024:.1f} MB")
    if files == 0:
        print("[pack_backend] ERROR: 没有打包任何文件", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
