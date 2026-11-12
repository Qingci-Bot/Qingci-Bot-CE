# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""统一错误模型

框架内所有领域错误继承 `AppError`，携带机器可读错误码（`code`），
便于 WebUI / 日志 / API 按错误码分类展示与统计。

兼容性约定：已有错误类通过**多继承**并入（如 `TelegramAPIError(AppError,
RuntimeError)`），原有 isinstance 语义（ValueError / RuntimeError / Exception）
与 `str(e)` 输出全部保持不变，存量 `except` / `pytest.raises` 不受影响。

新增领域错误时应继承 `AppError`（或对应领域子类），并赋予独立的 `code`；
禁止在内核代码新抛裸 `ValueError` / `RuntimeError` 表达领域失败。
"""

from __future__ import annotations


class AppError(Exception):
    """框架统一错误基类

    Attributes:
        code: 机器可读错误码（类属性，子类覆盖；WebUI 按此分类展示）
        detail: 可选的补充信息（不影响 str(e) 输出）
    """

    code: str = "app_error"

    def __init__(self, *args: object, detail: str | None = None) -> None:
        super().__init__(*args)
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        """结构化输出（供 API / 日志使用；message 取 str(self)）"""
        payload = {"code": self.code, "message": str(self)}
        if self.detail:
            payload["detail"] = self.detail
        return payload


# ---- 插件域 ----


class PluginLoadError(AppError, ValueError):
    """插件加载/注册校验失败（模块无插件类、多插件类、注册名替换失败等）"""

    code = "plugin_load_error"


class PluginDependencyError(PluginLoadError):
    """插件依赖解析失败（缺失、循环依赖、版本约束不满足）"""

    code = "plugin_dependency_error"


class PluginInstallError(AppError, ValueError):
    """插件安装失败（来源校验、归档安全解压、目录落位等；安装器抛出）"""

    code = "plugin_install_error"


# ---- 市场域 ----


class MarketError(AppError):
    """插件市场错误（索引拉取/解析/安装编排失败）"""

    code = "market_error"


# ---- 渲染域 ----


class HtmlRenderError(AppError):
    """HTML 渲染错误基类（能力不可用 / 超时 / 渲染失败）"""

    code = "html_render_error"
