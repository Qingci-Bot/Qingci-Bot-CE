# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""P0 跨仓库契约：CE 协议层薄转发与 Plugins-SDK 导出面保持一致

SDK 是协议层唯一来源，CE 的 bot/plugin/protocol/* 与顶层兼容模块都只能是
再导出。任一符号变成副本、改名或消失，都会让内置插件与外部插件走两条路径。
"""

import importlib

import pytest
import qingci_plugin_sdk as sdk

_MODULES = {
    "base": "qingci_plugin_sdk.base",
    "context": "qingci_plugin_sdk.context",
    "events": "qingci_plugin_sdk.events",
    "matcher": "qingci_plugin_sdk.matcher",
    "permission": "qingci_plugin_sdk.permission",
    "ratelimit": "qingci_plugin_sdk.ratelimit",
    "rule": "qingci_plugin_sdk.rule",
    "session": "qingci_plugin_sdk.session",
}

# bot/plugin/ 下存在同名兼容文件的模块；context 没有顶层别名，故不在此列
_TOP_LEVEL = ("base", "events", "matcher", "permission", "ratelimit", "rule", "session")

_CRITICAL_EXPORTS = (
    "PluginBase",
    "PluginStatus",
    "Matcher",
    "MatcherContext",
    "MessageContext",
    "Rule",
    "Permission",
    "RateLimiter",
    "Session",
    "I18n",
    "llm_tool",
    "on_command",
    "on_notice",
    "on_request",
    "subcommand",
    "keyword",
    "command",
    "Message",
    "MessageSegment",
    "parse_cq_string",
    "translate_v11_event",
    "to_v11_segment",
    "segments_to_v11",
)


@pytest.mark.parametrize("short, sdk_module", list(_MODULES.items()))
def test_protocol_module_is_pure_reexport(short, sdk_module):
    ce = importlib.import_module(f"bot.plugin.protocol.{short}")
    upstream = importlib.import_module(sdk_module)
    names = getattr(ce, "__all__", None)
    assert names, f"bot.plugin.protocol.{short} 未声明 __all__"
    for name in names:
        assert hasattr(upstream, name), f"{short}.{name} 在 SDK 中不存在"
        assert getattr(ce, name) is getattr(upstream, name), (
            f"{short}.{name} 不再是 SDK 的同一个对象（出现副本实现）"
        )


@pytest.mark.parametrize("short", _TOP_LEVEL)
def test_top_level_compat_module_reexports(short):
    ce = importlib.import_module(f"bot.plugin.protocol.{short}")
    top = importlib.import_module(f"bot.plugin.{short}")
    for name in getattr(ce, "__all__", []):
        assert hasattr(top, name), f"bot.plugin.{short} 缺少兼容导出 {name}"
        assert getattr(top, name) is getattr(ce, name)


def test_installed_sdk_version_matches_pyproject_pin():
    # SDK 钉版契约已升级为区间 + ABI 能力探测（tests/test_abi.py）：
    # - pyproject 声明与 abi.SDK_ABI_RANGE 同步
    # - 安装版本落在区间内且符号清单齐全
    # 此处仅保留环境哨兵：SDK 必须已安装
    assert sdk.__version__


def test_sdk_critical_exports_present():
    missing = [name for name in _CRITICAL_EXPORTS if not hasattr(sdk, name)]
    assert not missing, f"SDK 缺少 CE 依赖的关键符号: {missing}"


def test_sdk_module_level_all_matches_package_exports():
    """声明了模块级 __all__ 的模块，其导出必须在包级可见（星号导入依赖它）"""
    checked = 0
    for sdk_module in _MODULES.values():
        mod = importlib.import_module(sdk_module)
        names = getattr(mod, "__all__", None)
        if not names:
            # 未声明模块级 __all__ 的模块由 pure_reexport 用例逐个校验
            continue
        checked += 1
        missing = [n for n in names if not hasattr(sdk, n) and n != "__version__"]
        assert not missing, f"{sdk_module} 的模块级导出未在包级出现: {missing}"
    assert checked > 0
