# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""插件加载辅助函数（无状态纯函数与常量）

从 manager.py 拆出的模块级辅助：内置插件清单、子指令展开、
依赖版本声明解析、plugin.json 读取、插件类识别。
均不持有状态，供 PluginManager 加载/注册流程调用。
"""

import json
import logging
import re
from pathlib import Path
from typing import cast

from packaging.specifiers import SpecifierSet

from .protocol.base import PluginBase
from .protocol.matcher import Matcher

logger = logging.getLogger("qingci-bot.plugin.manager")

# 内置插件显式清单（PyInstaller 打包后 pkgutil 无法扫描 PYZ 归档内模块，
# 需回退到显式清单加载；新增内置插件时同步更新此处与 qingci-bot-ce.spec 的 hiddenimports）
_BUILTIN_PLUGINS: tuple[str, ...] = ("admin", "chat", "help", "imagegen", "knowledge")


def _expand_subcommand_matchers(matchers: list[Matcher], owner: str) -> None:
    """展开 on_command 子指令 matcher：parent.meta["sub_matchers"] → matchers

    SDK 在 on_command 创建 parent 时把全部子指令 matcher 挂到
    `parent.meta["sub_matchers"]`，覆盖两类注册场景：
    - 模块级（import 阶段）：子 matcher 已被收集器收集进 matchers → 按对象 id
      去重，避免双份；
    - on_load 运行时注册：子 matcher 仅挂在 parent.meta → 随 parent 展开，
      修复"父指令已排除子指令、子指令 matcher 又不注册"导致的子命令不触发。
    """
    known_ids = {id(m) for m in matchers}
    for m in list(matchers):
        if "command" in m.meta and "sub_matchers" not in m.meta:
            # SDK < 1.13.1 不挂 sub_matchers：on_load 运行时注册的子指令会静默丢失
            logger.warning(
                f"插件 {owner} 的命令 matcher {m.meta.get('command')!r} 缺少 "
                f"sub_matchers 元数据（qingci-plugin-sdk 版本过旧，需 >=1.13.1），"
                f"on_load 注册的子指令可能不触发"
            )
        for sub in m.meta.get("sub_matchers") or []:
            if id(sub) in known_ids:
                continue
            if not sub.owner:
                sub.owner = owner
            matchers.append(sub)
            known_ids.add(id(sub))


def _parse_version_spec(dep_spec: str) -> tuple[str, SpecifierSet | None]:
    """解析依赖声明，返回 (name, version_spec)。

    示例：
        "chat"           -> ("chat", None)
        "chat>=1.0"      -> ("chat", SpecifierSet(">=1.0"))
        "chat>=1.0,<2.0" -> ("chat", SpecifierSet(">=1.0,<2.0"))
    """
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9_]*)\s*(.*)$", dep_spec)
    if not match:
        raise ValueError(f"无效的依赖声明: {dep_spec}")
    name = match.group(1)
    spec_str = match.group(2).strip()
    if spec_str:
        try:
            return name, SpecifierSet(spec_str)
        except Exception:
            raise ValueError(f"无效的版本约束: {dep_spec}") from None
    return name, None


def _load_plugin_json(directory: Path) -> dict | None:
    """从目录中加载 plugin.json 元数据（若存在）"""
    json_path = directory / "plugin.json"
    if not json_path.is_file():
        return None
    try:
        return cast(dict, json.loads(json_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        logger.warning(f"plugin.json 解析失败: {json_path}")
        return None


def _sdk_plugin_base():
    """惰性获取独立插件 SDK 的 PluginBase 基类（SDK 未安装时返回 None）

    外部插件可能基于 bot.plugin.base.PluginBase（内置式）或
    qingci_plugin_sdk.base.PluginBase（独立 SDK 式）开发，两者是镜像类、
    无继承关系。管理器需同时识别这两类插件。
    """
    try:
        from qingci_plugin_sdk.base import PluginBase as _SdkBase

        return _SdkBase
    except ImportError:
        return None


def _module_plugin_classes(module, sdk_base) -> list[type]:
    """返回模块中定义的插件类（bot 基类或 SDK 基类的直接子类）"""
    result: list[type] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if not isinstance(attr, type) or attr.__module__ != module.__name__:
            continue
        if attr is PluginBase or (sdk_base is not None and attr is sdk_base):
            continue
        if issubclass(attr, PluginBase):
            result.append(attr)
        elif sdk_base is not None and issubclass(attr, sdk_base):
            result.append(attr)
    return result
