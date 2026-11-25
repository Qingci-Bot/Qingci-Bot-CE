# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""macOS（仅 Apple Silicon）打包契约

macOS 的坑集中在「配置错一行，产物看起来正常但用户装不上/打不开」，
且本地（Linux / CI 的 Linux 作业）完全无法察觉。这里把关键前提钉成断言：

1. **只出 arm64**：混进 x64 切片等于给用户一个跑不了的应用。
2. **必须 ad-hoc 签名**：Apple Silicon 对 arm64 原生代码强制要求有效签名，
   与 Gatekeeper 无关；没有签名时双击只显示「已损坏，无法打开」。本项目没有
   Apple Developer 证书，因此走 `identity: "-"`，且必须带
   ``disable-library-validation`` —— 否则 Electron 自带的（Apple 签名的）
   Framework 会被 hardened runtime 的库校验拒绝，启动即崩。
3. **签名发生在造 dmg 之前**：由 electron-builder 完成。若改成「先出 dmg 再补签」，
   dmg 里装的仍是未签名应用。
4. **图标 ≥512×512**：macOS 图标要从大图生成 .icns，低于 512 会直接构建失败。
"""

from __future__ import annotations

import json
import plistlib
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG_JSON = ROOT / "desktop" / "electron" / "package.json"
ENTITLEMENTS = ROOT / "desktop" / "electron" / "build" / "entitlements.mac.plist"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_macos_app.sh"
BUILD_UNIX = ROOT / "build-unix.sh"
BUILD_LINUX = ROOT / "build-linux.sh"
SPEC = ROOT / "qingci-bot-ce.spec"


@pytest.fixture(scope="module")
def mac_cfg() -> dict:
    build = json.loads(PKG_JSON.read_text(encoding="utf-8"))["build"]
    cfg = build.get("mac")
    assert cfg, "package.json 缺少 build.mac 配置"
    return cfg


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"不是 PNG: {path}"
    return struct.unpack(">II", data[16:24])


def test_targets_arm64_only(mac_cfg):
    """只允许 arm64 目标（Intel Mac 不在支持范围）"""
    targets = mac_cfg["target"]
    archs = {arch for t in targets for arch in t.get("arch", [])}
    assert archs == {"arm64"}, f"macOS 目标架构应为 arm64，实际 {archs}"
    names = {t["target"] for t in targets}
    assert names == {"dmg", "zip"}, f"macOS 产物应为 dmg + zip，实际 {names}"


def test_uses_adhoc_signing(mac_cfg):
    """必须显式 ad-hoc 签名。

    `identity: null`（或留空且无证书）会让 electron-builder **完全跳过签名**，
    而 Apple Silicon 会拒绝启动未签名的 arm64 应用。
    """
    assert mac_cfg.get("identity") == "-", (
        'mac.identity 必须为 "-"（ad-hoc 签名）；null 会跳过签名，'
        "arm64 产物在 Apple Silicon 上打不开"
    )


def test_adhoc_signing_has_required_entitlements(mac_cfg):
    """ad-hoc + hardened runtime 必须关闭库校验，否则启动即崩"""
    assert mac_cfg.get("entitlements"), "缺少 mac.entitlements"
    ent_path = ROOT / "desktop" / "electron" / mac_cfg["entitlements"]
    assert ent_path.is_file(), f"entitlements 文件不存在: {ent_path}"

    with ent_path.open("rb") as f:
        ent = plistlib.load(f)
    assert ent.get("com.apple.security.cs.disable-library-validation") is True, (
        "必须开启 disable-library-validation：ad-hoc 签名没有 Team ID，"
        "而 Electron 的 Framework 带 Apple Team ID，库校验会拒绝加载"
    )
    # V8 需要 JIT
    assert ent.get("com.apple.security.cs.allow-jit") is True


def test_icon_is_large_enough(mac_cfg):
    """macOS 图标需 ≥512×512（electron-builder 据此生成 .icns）"""
    icon = ROOT / "desktop" / "electron" / mac_cfg["icon"]
    assert icon.is_file(), f"图标不存在: {icon}"
    w, h = _png_size(icon)
    assert min(w, h) >= 512, f"macOS 图标至少 512×512，实际 {w}×{h}"


def test_minimum_system_version_matches_arm64_baseline(mac_cfg):
    """Apple Silicon 起点是 macOS 11，声明更低没有意义、更高会挡住用户"""
    assert mac_cfg.get("minimumSystemVersion") == "11.0"


def test_verify_script_checks_signature_and_arch():
    """校验脚本必须真的断言架构与签名，而不只是「文件存在」"""
    assert VERIFY_SCRIPT.is_file(), "缺少 scripts/verify_macos_app.sh"
    text = VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert "lipo -archs" in text, "应读取 Mach-O 架构（不能只看文件名）"
    assert "arm64" in text
    assert "x86_64" in text, "应显式拒绝 x86_64 切片"
    assert "codesign --verify" in text, "应验证签名有效性"
    assert "Signature=adhoc" in text, "应断言使用 ad-hoc 签名"
    assert "--help" in text, "应真跑一次内嵌后端，证明「装完能启动」"


def test_build_script_is_platform_agnostic():
    """构建脚本要同时服务 Linux 与 macOS

    macOS 没有 GNU 的 sha256sum（只有 shasum -a 256），不处理会在最后生成
    清单的步骤报错，前面的构建全部白做。
    """
    assert BUILD_UNIX.is_file(), "缺少 build-unix.sh"
    text = BUILD_UNIX.read_text(encoding="utf-8")
    assert "shasum -a 256" in text, "缺少 macOS 的 sha256 回退实现"
    assert "sha256sum" in text


def test_build_linux_wrapper_still_works():
    """build-linux.sh 保留为兼容转发，老脚本/老文档不会突然失效"""
    assert BUILD_LINUX.is_file(), "build-linux.sh 兼容转发丢失"
    text = BUILD_LINUX.read_text(encoding="utf-8")
    assert "build-unix.sh" in text and "exec" in text


def test_spec_skips_icon_on_darwin():
    """PyInstaller 在 macOS 上要求 .icns，传 .ico 会报错；后端也无需图标"""
    text = SPEC.read_text(encoding="utf-8")
    assert "darwin" in text, "spec 未对 darwin 做图标分支"
    assert "_ICON = ''" in text or '_ICON = ""' in text


def test_electron_strips_quarantine_on_darwin():
    """解压出的后端要清 quarantine，否则首启动可能被 Gatekeeper 拦下"""
    main_js = (ROOT / "desktop" / "electron" / "src" / "main.js").read_text(encoding="utf-8")
    assert "com.apple.quarantine" in main_js
    assert "xattr" in main_js
