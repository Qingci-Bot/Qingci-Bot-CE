# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""GitHub Actions workflow 语义校验 —— 挡住 YAML 语法正确但语义会挂的改动

pre-commit 的 check-yaml 只保证「能解析」，抓不到下面这类错误：它们 YAML 完全
合法，但工作流会在 GitHub 上排队失败或静默做出错误的东西。发版流水线改错的
代价尤其高（要么卡在发版当天才发现，要么产出一个看起来正常、内容架构错误的包），
所以在这里用测试兜住：

1. `runs-on` 使用了不存在的 runner 标签（例如把 `ubuntu-22.04-arm` 拼成
   `ubuntu-arm`）——这类标签不会报错，只会一直 pending 到超时。
2. `needs` 指向不存在的 job —— YAML 合法，但整个 workflow 判定为无效，
   连触发都不会触发，最难发现。
3. step 既没有 `uses` 也没有 `run` —— 空步骤，跑起来才知道。
4. 发版 job 的架构产物与 `runs-on` 架构自相矛盾（本质是第 1 条的人肉版检查，
   但我们把它写死成对 release.yml 的明确断言，防止有人「临时改回 QEMU 方案」
   却忘了改产物校验参数）。

这些断言只针对 workflow 文件本身，不需要 runner，能在每次 PR 的 pytest 里跑。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# GitHub 托管 runner 的合法标签（截至 2026-11）。只收录本项目可能用到的系列：
# 若将来引入新标签而这里没收录，测试会失败并提示补充——刻意如此，避免有人
# 随手写一个「看着像」的标签。自托管 runner 请以 `self-hosted` 开头。
KNOWN_RUNNERS = {
    # x64 Linux
    "ubuntu-latest",
    "ubuntu-22.04",
    "ubuntu-24.04",
    "ubuntu-26.04",
    # arm64 Linux（公开仓库免费且不限量，见 .github/workflows/release.yml 注释）
    "ubuntu-22.04-arm",
    "ubuntu-24.04-arm",
    "ubuntu-26.04-arm",
    # Windows / macOS
    "windows-latest",
    "windows-2022",
    "windows-2025",
    "macos-latest",
    "macos-14",
    "macos-15",
}


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.y*ml"))


def _load(path: Path) -> dict:
    # GitHub Actions 允许 `on:` 被 YAML 解析成布尔 True，这里不处理——
    # 我们只关心 jobs，取 jobs 即可绕开该歧义。
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _all_jobs() -> list[tuple[str, str, dict]]:
    """(workflow 文件名, job id, job 定义) 三元组列表"""
    out = []
    for path in _workflow_files():
        for job_id, job in (_load(path).get("jobs") or {}).items():
            out.append((path.name, job_id, job or {}))
    return out


def test_workflow_files_are_parseable() -> None:
    """所有 workflow 都能被解析，且包含 jobs 段"""
    files = _workflow_files()
    assert files, "未找到任何 workflow 文件，路径是否写错？"
    for path in files:
        data = _load(path)
        assert isinstance(data, dict), f"{path.name} 顶层不是映射"
        assert data.get("jobs"), f"{path.name} 缺少 jobs 段"


def test_runs_on_uses_known_runner_labels() -> None:
    """runs-on 必须是已知 runner 标签（拼错的标签会一直 pending 到超时）"""
    bad: list[str] = []
    for wf, job_id, job in _all_jobs():
        runs_on = job.get("runs-on")
        if runs_on is None:
            continue
        labels = runs_on if isinstance(runs_on, list) else [runs_on]
        for label in labels:
            # 表达式（${{ ... }}）与自托管标签不做白名单校验：
            # 前者运行时才能求值，后者由仓库所有者自行注册。
            if not isinstance(label, str):
                continue
            if "${{" in label or label.startswith("self-hosted"):
                continue
            if label not in KNOWN_RUNNERS:
                bad.append(f"{wf}:{job_id} runs-on={label!r}")
    assert not bad, (
        "runs-on 使用了未知标签（若确为新增的合法 runner，请补进 KNOWN_RUNNERS）：\n"
        + "\n".join(bad)
    )


def test_needs_refer_to_existing_jobs() -> None:
    """needs 指向的 job 必须在同一文件里存在（否则整个 workflow 无效）"""
    bad: list[str] = []
    for path in _workflow_files():
        jobs = _load(path).get("jobs") or {}
        for job_id, job in jobs.items():
            job = job or {}
            needs = job.get("needs")
            if needs is None:
                continue
            refs = needs if isinstance(needs, list) else [needs]
            for ref in refs:
                if ref not in jobs:
                    bad.append(f"{path.name}:{job_id} needs={ref!r}（不存在）")
    assert not bad, "needs 引用了不存在的 job:\n" + "\n".join(bad)


def test_steps_have_uses_or_run() -> None:
    """每个 step 必须有 uses 或 run，否则是空步骤"""
    bad: list[str] = []
    for wf, job_id, job in _all_jobs():
        for idx, step in enumerate(job.get("steps") or [], 1):
            if not isinstance(step, dict):
                bad.append(f"{wf}:{job_id} 第 {idx} 步不是映射")
                continue
            if not (step.get("uses") or step.get("run")):
                bad.append(f"{wf}:{job_id} 第 {idx} 步既无 uses 也无 run")
    assert not bad, "存在无效步骤:\n" + "\n".join(bad)


def test_release_jobs_set_timeout() -> None:
    """发版构建 job 必须设 timeout-minutes

    发版 job 会真装一遍包、跑冒烟，卡死时默认 6 小时超时才结束，会白白占住
    队列并拖延发版。显式设上限让失败尽早暴露。
    """
    release = WORKFLOW_DIR / "release.yml"
    if not release.is_file():
        pytest.skip("release.yml 不存在")
    missing = [
        job_id
        for job_id, job in (_load(release).get("jobs") or {}).items()
        if job and job.get("runs-on") and not job.get("timeout-minutes")
    ]
    assert not missing, f"release.yml 中以下 job 缺少 timeout-minutes: {missing}"


def test_arm64_job_builds_natively() -> None:
    """release.yml 的 arm64 job 必须跑在原生 arm64 runner 上，不得退回 QEMU

    背景见 release.yml 的 job 注释：v1.22.4 之前用「x64 + QEMU 容器」构建，
    单是后端构建就超过 60 分钟；换原生 ubuntu-22.04-arm 后整条链 12 分钟。
    这里把「不用 QEMU」钉成断言，防止后续改动无意中退回去。
    """
    release = WORKFLOW_DIR / "release.yml"
    if not release.is_file():
        pytest.skip("release.yml 不存在")
    data = _load(release)
    job = (data.get("jobs") or {}).get("linux-arm64")
    assert job, "release.yml 缺少 linux-arm64 job"

    assert job.get("runs-on") == "ubuntu-22.04-arm", (
        "linux-arm64 应跑在原生 ubuntu-22.04-arm 上（与 x64 同代，保住 glibc 2.35 基线），"
        f"当前为 {job.get('runs-on')!r}"
    )

    steps = job.get("steps") or []
    qemu = [
        s
        for s in steps
        if isinstance(s, dict)
        and (
            "qemu" in str(s.get("uses", "")).lower()
            or "--platform linux/arm64" in str(s.get("run", ""))
        )
    ]
    assert not qemu, f"linux-arm64 不应再使用 QEMU 模拟构建，发现: {qemu}"

    # 产物架构断言：verify 脚本必须按 arm64/aarch64 校验，否则会放过错误的包
    verify_runs = " ".join(str(s.get("run", "")) for s in steps if isinstance(s, dict))
    assert "verify_linux_packages.sh" in verify_runs, "linux-arm64 缺少包元数据校验步骤"
    assert "arm64 aarch64" in verify_runs, (
        "linux-arm64 的包校验参数应为 `arm64 aarch64`（deb 架构 / rpm 架构），"
        "写错会让 x64 包以 arm64 之名发布"
    )


def test_macos_job_targets_apple_silicon() -> None:
    """macOS 只发布 Apple Silicon：job 必须钉在 macos-15（arm64）并自行断言

    为什么不用 `macos-latest`：它的架构随 GitHub 镜像策略漂移（曾从 macos-13
    的 x64 切到 macos-14 的 arm64）。PyInstaller 不能交叉编译，落到 x64 runner
    就会产出 x64 后端——名字写着 arm64、用户装上却跑不了。macos-13 明确是 x64，
    同样不允许。

    为什么是 15 而不是 14（实测踩到）：本仓库请求 `macos-14` 会拿到注解
    「The configuration 'macos-14-arm64-us-default' is not supported」，并在
    1 秒内被取消（runner_id=0、0 个步骤）。矩阵探测里 macos-15 与 macos-latest
    成功、macos-14 取消，故钉在 macos-15。
    """
    release = WORKFLOW_DIR / "release.yml"
    if not release.is_file():
        pytest.skip("release.yml 不存在")
    job = (_load(release).get("jobs") or {}).get("macos-dmg")
    assert job, "release.yml 缺少 macos-dmg job"
    assert job.get("runs-on") == "macos-15", (
        f"macos-dmg 应跑在 macos-15（Apple Silicon），当前为 {job.get('runs-on')!r}"
    )

    runs = [str(s.get("run", "")) for s in job.get("steps") or [] if isinstance(s, dict)]
    joined = "\n".join(runs)
    # 必须有架构断言：标签漂移时立刻失败，而不是产出架构错误的包
    assert "uname -m" in joined and "arm64" in joined, "macos-dmg 缺少 arm64 宿主断言"


def test_macos_job_verifies_signed_arm64_bundle() -> None:
    """macOS 产物必须校验签名与架构

    Apple Silicon 对 arm64 原生代码强制要求有效签名，与 Gatekeeper 无关：
    未签名（或签名被后续步骤破坏）的包，用户双击只会看到「已损坏，无法打开」，
    而 CI 里「构建成功」看起来一切正常。所以发版链必须跑
    scripts/verify_macos_app.sh，而不是打完 dmg 就上传。
    """
    release = WORKFLOW_DIR / "release.yml"
    if not release.is_file():
        pytest.skip("release.yml 不存在")
    job = (_load(release).get("jobs") or {}).get("macos-dmg") or {}
    joined = "\n".join(str(s.get("run", "")) for s in job.get("steps") or [] if isinstance(s, dict))
    assert "verify_macos_app.sh" in joined, "macos-dmg 缺少 app bundle 校验步骤"
    # 上传的产物名必须带 arm64，避免与 x64 混淆
    assert "-macos-arm64.dmg" in joined, "上传步骤应校验 macOS dmg 命名（含平台标识）"


def test_checksums_waits_for_all_platforms() -> None:
    """校验和必须等全部平台构建成功，否则会生成缺文件的 SHA256SUMS.txt"""
    release = WORKFLOW_DIR / "release.yml"
    if not release.is_file():
        pytest.skip("release.yml 不存在")
    job = (_load(release).get("jobs") or {}).get("checksums") or {}
    needs = job.get("needs") or []
    for platform in ("windows-exe", "linux-deb-rpm", "linux-arm64", "macos-dmg"):
        assert platform in needs, f"checksums.needs 缺少 {platform}"


def test_linux_jobs_normalize_artifacts_before_verify() -> None:
    """两个 Linux job 都必须先重命名产物、再校验文件名

    重命名的意义是让发布页上每个文件都带平台与架构标识；校验脚本断言的正是
    新命名。顺序反了就会拿旧名去比对，必然失败——但失败信息会指向「命名不符」，
    掩盖真正的原因（步骤顺序）。
    """
    release = WORKFLOW_DIR / "release.yml"
    if not release.is_file():
        pytest.skip("release.yml 不存在")
    jobs = _load(release).get("jobs") or {}
    for job_name in ("linux-deb-rpm", "linux-arm64"):
        steps = [s for s in (jobs[job_name].get("steps") or []) if isinstance(s, dict)]
        names = [str(s.get("name", "")) for s in steps]
        norm_idx = next((i for i, n in enumerate(names) if "Normalize artifact names" in n), None)
        assert norm_idx is not None, f"{job_name} 缺少重命名步骤"
        verify_idx = next((i for i, n in enumerate(names) if "Verify package metadata" in n), None)
        assert verify_idx is not None, f"{job_name} 缺少校验步骤"
        assert norm_idx < verify_idx, f"{job_name} 必须先重命名再校验"
