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
