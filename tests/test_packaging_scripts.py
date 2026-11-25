# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""打包脚本行为契约（不只是「文件里有某段文字」）

## 为什么需要这一层

1.23.0-rc1 发版时 arm64 的 deb/rpm 打包失败：

    dpkg-deb: 'Version' 字段值 'v1.23.0~rc1': 版本号没有以数字开头

原因是 arm64 job 重写时漏掉了 `${ver#v}`——tag 是 `v1.23.0-rc1`，直接传给
`pack_linux_server.sh`，dpkg 拒绝以字母开头的版本号。

**更值得记住的是它为什么没被提前发现**：arm64 原生 runner 的试用跑是手工传
`--version 1.22.4`（没有 `v`）的，从未走过「由 tag 注入版本号」这条真实路径。
所以这里既测脚本自身的容错，也测工作流确实做了剥离——两条路都堵上。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PACK_SERVER = ROOT / "scripts" / "pack_linux_server.sh"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def test_pack_server_tolerates_tag_prefix_in_source() -> None:
    """脚本自身必须剥掉 tag 风格的 v 前缀（调用方忘一次不该产出坏包）"""
    text = PACK_SERVER.read_text(encoding="utf-8")
    assert 'VERSION="${VERSION#v}"' in text, (
        "pack_linux_server.sh 未剥离 v 前缀；dpkg 要求 Version 以数字开头，"
        "传 v1.23.0-rc1 会在 deb 阶段直接失败"
    )


def test_linux_jobs_strip_tag_prefix_before_calling() -> None:
    """两个 Linux job 调用打包脚本时也必须剥掉 v

    脚本自身虽已容错，但工作流显式剥离能保持与 x64 链路一致、也避免
    其他消费方（文档示例、本地脚本）照抄出错的写法。
    """
    jobs = (yaml.safe_load(RELEASE.read_text(encoding="utf-8")) or {}).get("jobs") or {}
    offenders: list[str] = []
    for job_name in ("linux-deb-rpm", "linux-arm64"):
        job = jobs.get(job_name) or {}
        # 只认「真的传了 --version 去调用」的步骤：注释里提到脚本名的步骤
        # （如上传资产的说明）不构成调用
        calls = [
            str(step.get("run", ""))
            for step in job.get("steps") or []
            if isinstance(step, dict)
            and "pack_linux_server.sh" in str(step.get("run", ""))
            and "--version" in str(step.get("run", ""))
        ]
        assert calls, f"{job_name} 未调用 pack_linux_server.sh"
        for run in calls:
            if "${ver#v}" not in run:
                offenders.append(f"{job_name}: {run.strip()[:120]}")
    assert not offenders, (
        "以下调用未剥掉 tag 的 v 前缀（dpkg 会拒绝以字母开头的 Version）：\n" + "\n".join(offenders)
    )


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="需要 dpkg-deb 才能产出 deb")
def test_pack_server_output_has_no_leading_v(tmp_path: Path) -> None:
    """真实跑一次打包：带 v 输入也必须产出不带 v、且保留 -rc 的文件名

    这条是行为级验证——文本断言只能证明「写了那行」，证明不了「真的生效」。
    """
    bundle = tmp_path / "dist" / "qingci-bot-ce"
    bundle.mkdir(parents=True)
    exe = bundle / "qingci-bot-ce"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)

    out = tmp_path / "out"
    subprocess.run(
        [
            "bash",
            str(PACK_SERVER),
            "--version",
            "v1.23.0-rc1",
            "--only",
            "deb",
            "--bundle",
            str(bundle),
            "--out",
            str(out),
        ],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    names = sorted(p.name for p in out.iterdir())
    assert names, "未产出任何包"
    for name in names:
        assert "v1.23.0" not in name, f"文件名仍带 v 前缀: {name}"
        assert "1.23.0-rc1" in name, f"文件名应保留 -rc1（~ 会被 GitHub 改成 .）: {name}"

    # 包内 Version 用 ~ 表示预发布，保证 1.23.0~rc1 < 1.23.0（升级方向正确）
    deb = next(p for p in out.iterdir() if p.suffix == ".deb")
    meta = subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # dpkg-deb -f 指定单个字段时只返回值本身，不带 "Version: " 前缀
    assert meta == "1.23.0~rc1", f"包内版本应为 1.23.0~rc1，实际 {meta}"


# ──────────────────────────────────────────────────────────────────
# macOS / BSD 工具链的可移植性护栏
#
# 这两类问题本地 Linux 完全测不出来，只有真正在 macOS runner 上跑才暴露，
# 而一次发版要 20 分钟才发现——值得用静态检查提前拦住。
# ──────────────────────────────────────────────────────────────────

SHELL_SCRIPTS = [
    "build-unix.sh",
    "build-linux.sh",
    "scripts/verify_macos_app.sh",
    "scripts/pack_linux_server.sh",
    "scripts/smoke_install_packages.sh",
    "scripts/verify_linux_packages.sh",
]


def _shell_files() -> list[Path]:
    return [ROOT / p for p in SHELL_SCRIPTS if (ROOT / p).is_file()]


def test_no_unbraced_variable_before_non_ascii_in_shell() -> None:
    """shell 里 `$var` 后面紧跟中文时必须是 `${var}`

    macOS 自带的是 bash 3.2，它会把紧随其后的多字节字符当成变量名的一部分，
    报 `minos?: unbound variable`；Linux 的 bash 5 不会。于是这种写法在本地
    与 ubuntu CI 上都正常，只在 macOS 发版时炸——1.23.0-rc1 就踩了。

    这不是风格问题：只要变量出现在中文句子里（本项目的脚本全是中文提示），
    就一定会遇到。
    """
    pattern = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7F])")
    offenders: list[str] = []
    for path in _shell_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue  # 注释不参与展开
            for m in pattern.finditer(line):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{lineno}: ${m.group(1)} 后紧跟非 ASCII"
                    f" -> 改为 ${{{m.group(1)}}}"
                )
    assert not offenders, "以下位置在 macOS 的 bash 3.2 上会报 unbound variable：\n" + "\n".join(
        offenders
    )


def test_no_gnu_only_sort_in_macos_scripts() -> None:
    """macOS 脚本不得使用 `sort -V`

    `-V`（版本排序）是 GNU coreutils 扩展，macOS 自带的 BSD sort 不认，
    会直接报 illegal option。版本比较请用 awk 分段数值比较。
    """
    verify = ROOT / "scripts" / "verify_macos_app.sh"
    assert verify.is_file()
    # 只看可执行行：注释里说明「为什么不用 sort -V」是合法的
    code = [
        ln
        for ln in verify.read_text(encoding="utf-8").split("\n")
        if not ln.lstrip().startswith("#")
    ]
    assert "sort -V" not in "\n".join(code), (
        "verify_macos_app.sh 使用了 GNU 专有的 sort -V，macOS 的 BSD sort 不支持；"
        "请改用 awk 做版本比较"
    )


# ──────────────────────────────────────────────────────────────────
# 产物命名：<包名>-<版本>-<平台>-<架构>.<ext>
#
# 起因（用户反馈）：发布页上 `qingci-bot-ce-1.23.0-rc2-arm64.dmg` 看不出是
# macOS，`.zip` / `.exe` 也看不出是 Windows；deb/rpm 又分别用 amd64 / x86_64 /
# aarch64 三套架构写法。用户翻资产列表得靠猜。
# ──────────────────────────────────────────────────────────────────

PLATFORM_TOKENS = {"linux": "linux", "mac": "macos", "win": "windows"}


def test_electron_artifact_names_carry_platform_token():
    """electron-builder 的四个 artifactName 都必须带平台标识

    漏掉任何一个，那一个平台的文件就会退回旧命名（无平台标识），
    发布页上又出现「看不出这是哪个系统」的文件。
    """
    import json

    cfg = json.loads((ROOT / "desktop/electron/package.json").read_text(encoding="utf-8"))
    build = cfg["build"]

    # deb / rpm 有各自的 artifactName，会**覆盖** linux 级模板——这正是历史上
    # rpm 不理会 linux.artifactName、按 fpm 默认名输出的原因。所以四处都必须写，
    # 漏一处那个格式就退回旧命名。
    checks = [
        ("win", build["win"]["artifactName"], "windows"),
        ("nsis", build["nsis"]["artifactName"], "windows"),
        ("linux", build["linux"]["artifactName"], "linux"),
        ("deb", build["deb"]["artifactName"], "linux"),
        ("rpm", build["rpm"]["artifactName"], "linux"),
        ("mac", build["mac"]["artifactName"], "macos"),
    ]
    for key, pattern, token in checks:
        assert token in pattern, f"{key}.artifactName 缺少平台标识 {token}: {pattern}"
        assert "${version}" in pattern, f"{key}.artifactName 缺少版本占位符: {pattern}"


def test_normalize_linux_artifacts_renames_to_canonical_scheme(tmp_path):
    """把 electron-builder / fpm 的各种旧写法统一成 <包名>-<版本>-linux-<arch>"""
    version = "1.23.0-rc3"
    for name in (
        "qingci-bot-ce_1.23.0-rc3_amd64.deb",
        "qingci-bot-ce-1.23.0-rc3-1.x86_64.rpm",
        "qingci-bot-ce-server_1.23.0-rc3_amd64.deb",
        "qingci-bot-ce-server-1.23.0-rc3-1.x86_64.rpm",
    ):
        (tmp_path / name).touch()

    result = subprocess.run(
        [str(ROOT / "scripts/normalize_linux_artifacts.sh"), str(tmp_path), "v" + version, "x64"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [
            f"qingci-bot-ce-{version}-linux-x64.deb",
            f"qingci-bot-ce-{version}-linux-x64.rpm",
            f"qingci-bot-ce-server-{version}-linux-x64.deb",
            f"qingci-bot-ce-server-{version}-linux-x64.rpm",
        ]
    )


def test_normalize_linux_artifacts_is_idempotent(tmp_path):
    """重复执行不能叠出 -linux-x64-linux-x64（workflow 可能在两处调用）"""
    (tmp_path / "qingci-bot-ce-1.23.0-rc3-linux-x64.deb").touch()
    for _ in range(2):
        result = subprocess.run(
            [
                str(ROOT / "scripts/normalize_linux_artifacts.sh"),
                str(tmp_path),
                "1.23.0-rc3",
                "x64",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    assert [p.name for p in tmp_path.iterdir()] == ["qingci-bot-ce-1.23.0-rc3-linux-x64.deb"]


def test_normalize_linux_artifacts_keeps_server_prefix(tmp_path):
    """桌面包与服务端包不能互相覆盖：前缀必须保留"""
    (tmp_path / "qingci-bot-ce_1.23.0_amd64.deb").touch()
    (tmp_path / "qingci-bot-ce-server_1.23.0_amd64.deb").touch()
    subprocess.run(
        [str(ROOT / "scripts/normalize_linux_artifacts.sh"), str(tmp_path), "1.23.0", "x64"],
        check=True,
        capture_output=True,
    )
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "qingci-bot-ce-1.23.0-linux-x64.deb",
        "qingci-bot-ce-server-1.23.0-linux-x64.deb",
    ]
