# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""SDK ABI 能力探测（解除 qingci-plugin-sdk 版本硬钉）

主框架以固定符号清单消费 SDK（协议层薄转发 + core 的少量直接导入），
兼容性由**能力探测**而非精确版本钉死保证：

- ``SDK_ABI_RANGE``：允许的 SDK 版本区间（与 pyproject.toml 的依赖声明
  保持一致，test_abi.py 契约测试锁同步）；
- ``REQUIRED_SYMBOLS``：框架实际消费的全部 SDK 符号（模块 → 符号名），
  启动时逐一探测，缺失即给出明确报错点（而非 import 时的晦涩
  ImportError / 半初始化状态）；
- ``check_sdk_abi()``：版本区间 + 符号清单双检，返回问题列表（空即兼容）。

约定：
- 主框架新增 SDK 导入时，必须同步更新 ``REQUIRED_SYMBOLS``
  （test_abi.py 用 AST 扫描自动核对清单覆盖，漏更新即测试失败）；
- SDK 在区间内的 minor 升级只需跑通本探测 + 全量测试即可合入，
  无需改动 pyproject 钉版。
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

logger = logging.getLogger("qingci-bot.plugin.abi")

# SDK 兼容区间（与 pyproject.toml dependencies 中的声明一致，契约测试锁同步）
SDK_ABI_RANGE = ">=1.15.2,<2.0.0"

# 框架实际消费的 SDK 符号清单（模块 → 符号名）
# 来源：AST 扫描 bot/ 全部 `from qingci_plugin_sdk... import ...`（含 core 直接导入
# 与 protocol/ 薄转发）；新增导入必须同步此处，test_abi.py 会自动核对。
REQUIRED_SYMBOLS: dict[str, tuple[str, ...]] = {
    "qingci_plugin_sdk": ("resolve_images",),
    "qingci_plugin_sdk.base": ("PluginBase", "PluginStatus"),
    "qingci_plugin_sdk.context": ("MessageContext",),
    "qingci_plugin_sdk.events": (
        "FriendAddNotice",
        "FriendPokeNotice",
        "FriendRecallNotice",
        "FriendRequestEvent",
        "GroupAdminNotice",
        "GroupBanNotice",
        "GroupCardNotice",
        "GroupDecreaseNotice",
        "GroupEssenceNotice",
        "GroupHonorChangeNotice",
        "GroupIncreaseNotice",
        "GroupLuckyKingNotice",
        "GroupRecallNotice",
        "GroupRequestEvent",
        "GroupSignInNotice",
        "GroupUploadNotice",
        "MessageEditedEvent",
        "NoticeEvent",
        "PokeNotice",
        "RequestEvent",
        "detail_type_to_notice_type",
        "notice_type_to_detail_type",
        "parse_event",
        "parse_notice_event",
        "parse_request_event",
        "parse_v12_event",
        "translate_v11_event",
    ),
    "qingci_plugin_sdk.llm_tool": (
        "LlmToolSpec",
        "begin_tool_collection",
        "end_tool_collection",
        "llm_tool",
    ),
    "qingci_plugin_sdk.matcher": (
        "Matcher",
        "MatcherContext",
        "begin_module_collection",
        "end_module_collection",
        "on_command",
        "on_keyword",
        "on_message",
        "on_notice",
        "on_request",
        "on_startswith",
    ),
    "qingci_plugin_sdk.paths": ("set_data_root",),
    "qingci_plugin_sdk.permission": (
        "ADMIN",
        "EVERYONE",
        "GROUP",
        "GROUP_MEMBER",
        "MEMBER",
        "PRIVATE",
        "Permission",
        "SUPERUSER",
        "USER",
        "describe_permission",
    ),
    "qingci_plugin_sdk.ratelimit": ("RateLimiter",),
    "qingci_plugin_sdk.rule": (
        "Rule",
        "command",
        "contains",
        "endswith",
        "fullmatch",
        "is_group",
        "is_private",
        "keyword",
        "rate_limit",
        "regex",
        "startswith",
        "subcommand",
        "to_me",
    ),
    "qingci_plugin_sdk.segments": ("Message", "MessageSegment", "to_v11_segment"),
    "qingci_plugin_sdk.session": (
        "FinishException",
        "PauseException",
        "RejectException",
        "Session",
    ),
}


def get_installed_sdk_version() -> str | None:
    """已安装的 qingci-plugin-sdk 版本号；未安装返回 None"""
    try:
        return _pkg_version("qingci-plugin-sdk")
    except PackageNotFoundError:
        return None


def check_sdk_abi() -> list[str]:
    """SDK ABI 探测：版本区间 + 符号清单双检

    Returns:
        问题列表（元素为人类可读的问题描述）；空列表表示兼容。
        探测本身不抛异常——SDK 缺失/损坏都以问题项形式返回，
        由调用方决定是降级告警还是终止启动。
    """
    problems: list[str] = []

    ver = get_installed_sdk_version()
    if ver is None:
        problems.append("qingci-plugin-sdk 未安装（框架协议层与插件系统不可用）")
    else:
        try:
            parsed = Version(ver)
        except InvalidVersion:
            problems.append(f"qingci-plugin-sdk 版本号无效: {ver!r}")
        else:
            if parsed not in SpecifierSet(SDK_ABI_RANGE):
                problems.append(
                    f"qingci-plugin-sdk 版本 {ver} 不在兼容区间 {SDK_ABI_RANGE}"
                    "（升级后请运行全量测试确认协议层无破坏）"
                )

    for module_name, symbols in REQUIRED_SYMBOLS.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            problems.append(f"qingci-plugin-sdk 缺少模块 {module_name}")
            continue
        for symbol in symbols:
            if not hasattr(module, symbol):
                problems.append(f"qingci-plugin-sdk ABI 变更: {module_name}.{symbol} 缺失")

    return problems


def log_sdk_abi_status() -> None:
    """启动时记录 SDK 兼容状态（有问题时 ERROR 级，正常时 DEBUG 级）"""
    problems = check_sdk_abi()
    if problems:
        for problem in problems:
            logger.error(f"SDK ABI 探测失败: {problem}")
    else:
        ver = get_installed_sdk_version()
        logger.debug(f"SDK ABI 兼容: qingci-plugin-sdk {ver} ({SDK_ABI_RANGE})")
