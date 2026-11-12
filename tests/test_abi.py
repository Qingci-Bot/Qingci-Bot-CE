# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""SDK ABI 能力探测契约测试（P1：解除 qingci-plugin-sdk 版本硬钉）

锁四个契约：
1. 清单完整性：AST 扫描 bot/ 全部 SDK 导入，REQUIRED_SYMBOLS 必须全覆盖
   （框架新增 SDK 导入而漏更新清单时，本测试失败）
2. 版本同步：pyproject.toml 的 SDK 依赖声明与 abi.SDK_ABI_RANGE 一致
3. 环境健康：当前安装的 SDK 通过 check_sdk_abi（问题列表为空）
4. 探测有效：注入伪造的缺失符号 / 越界版本，check_sdk_abi 能报出
"""

import ast
import pathlib
import re
import tomllib

import pytest

from bot.plugin.abi import (
    REQUIRED_SYMBOLS,
    SDK_ABI_RANGE,
    check_sdk_abi,
    get_installed_sdk_version,
)

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _framework_sdk_imports() -> dict[str, set[str]]:
    """AST 扫描 bot/ 与 api/ 的全部 qingci_plugin_sdk 导入（模块 → 符号集）"""
    result: dict[str, set[str]] = {}
    for pkg_dir in (_REPO / "bot", _REPO / "api"):
        if not pkg_dir.is_dir():
            continue
        for path in pkg_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith("qingci_plugin_sdk")
                ):
                    names = result.setdefault(node.module, set())
                    for alias in node.names:
                        if alias.name != "*":
                            names.add(alias.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("qingci_plugin_sdk"):
                            result.setdefault(alias.name, set())
    return result


def test_abi_manifest_covers_framework_imports():
    """REQUIRED_SYMBOLS 覆盖框架全部 SDK 导入（模块与符号双向核对）"""
    imports = _framework_sdk_imports()
    # 模块级核对：每个被导入的模块都在清单里
    for module in imports:
        assert module in REQUIRED_SYMBOLS, f"清单缺少 SDK 模块: {module}"
    # 符号级核对：清单声明的符号 ⊇ 框架实际导入的符号
    for module, names in imports.items():
        missing = names - set(REQUIRED_SYMBOLS[module])
        assert not missing, (
            f"清单缺少 {module} 的符号: {sorted(missing)}（请同步 bot/plugin/abi.py）"
        )


def test_pyproject_pin_matches_abi_range():
    """pyproject.toml 的 SDK 依赖声明与 SDK_ABI_RANGE 同步"""
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    sdk_deps = [d for d in deps if d.startswith("qingci-plugin-sdk")]
    assert len(sdk_deps) == 1
    assert SDK_ABI_RANGE.replace(" ", "") in sdk_deps[0].replace(" ", ""), (
        f"pyproject 声明 {sdk_deps[0]!r} 与 SDK_ABI_RANGE {SDK_ABI_RANGE!r} 不一致"
    )


def test_installed_sdk_passes_probe():
    """当前安装的 SDK 通过完整探测（版本 + 符号）"""
    assert get_installed_sdk_version() is not None
    problems = check_sdk_abi()
    assert problems == [], f"当前 SDK 环境不兼容: {problems}"


def test_probe_detects_missing_symbol(monkeypatch):
    """清单注入伪造符号时，探测必须报出（探测有效性）"""
    monkeypatch.setitem(REQUIRED_SYMBOLS, "qingci_plugin_sdk.segments", ("Message", "__bogus__"))
    problems = check_sdk_abi()
    assert any("__bogus__" in p and "segments" in p for p in problems)


def test_probe_detects_missing_module(monkeypatch):
    """清单注入不存在的模块时，探测必须报出"""
    monkeypatch.setitem(REQUIRED_SYMBOLS, "qingci_plugin_sdk.__bogus_mod__", ("x",))
    problems = check_sdk_abi()
    assert any("__bogus_mod__" in p for p in problems)


def test_probe_detects_out_of_range_version(monkeypatch):
    """版本号被伪造成越界值时，探测必须报出"""
    monkeypatch.setattr("bot.plugin.abi.get_installed_sdk_version", lambda: "9.9.9")
    problems = check_sdk_abi()
    assert any("兼容区间" in p for p in problems)


def test_probe_detects_missing_sdk(monkeypatch):
    """SDK 未安装（返回 None）时，探测必须报出"""
    monkeypatch.setattr("bot.plugin.abi.get_installed_sdk_version", lambda: None)
    problems = check_sdk_abi()
    assert any("未安装" in p for p in problems)


def test_range_is_bounded():
    """兼容区间必须有上界（防主版本破坏性变更静默引入）"""
    assert re.match(r"^>=\d", SDK_ABI_RANGE)
    assert "<" in SDK_ABI_RANGE, "SDK_ABI_RANGE 缺少版本上界"


@pytest.mark.parametrize(
    "module,expected",
    [
        ("qingci_plugin_sdk.base", "PluginBase"),
        ("qingci_plugin_sdk.segments", "Message"),
        ("qingci_plugin_sdk.events", "translate_v11_event"),
    ],
)
def test_probe_required_symbols_importable(module, expected):
    """清单中的关键符号在当前 SDK 中真实存在"""
    import importlib

    assert hasattr(importlib.import_module(module), expected)
