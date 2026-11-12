# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""统一错误模型契约测试（P1）

锁定三个契约：
1. 既有错误类全部并入 AppError 体系（isinstance 链不破坏存量 except / pytest.raises）
2. str(e) 输出保持不变（消息格式是既有测试与日志的依赖）
3. 每个错误类携带独立的机器可读 code（供 WebUI / API 分类）
"""

import pytest

from bot.core.events import EventContractError
from bot.core.platforms.telegram import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNotFoundError,
    TelegramUnauthorizedError,
)
from bot.errors import (
    AppError,
    HtmlRenderError,
    MarketError,
    PluginDependencyError,
    PluginInstallError,
    PluginLoadError,
)
from bot.html_renderer import (
    HtmlRenderTimeoutError,
    HtmlRenderUnavailableError,
)
from bot.plugin.market import MarketError as MarketErrorReexport


def test_all_domain_errors_are_app_error():
    """既有错误类全部是 AppError 子类"""
    for cls in (
        EventContractError,
        TelegramAPIError,
        TelegramUnauthorizedError,
        TelegramForbiddenError,
        TelegramNotFoundError,
        HtmlRenderError,
        HtmlRenderUnavailableError,
        HtmlRenderTimeoutError,
        MarketError,
        PluginLoadError,
        PluginDependencyError,
        PluginInstallError,
    ):
        assert issubclass(cls, AppError), cls


def test_builtin_exception_semantics_preserved():
    """多继承保持既有内置异常语义（存量 except / pytest.raises 兼容）"""
    assert issubclass(EventContractError, ValueError)
    assert issubclass(TelegramAPIError, RuntimeError)
    assert issubclass(MarketError, Exception)
    assert issubclass(HtmlRenderError, Exception)
    # 插件加载/安装错误保持 ValueError 语义
    assert issubclass(PluginLoadError, ValueError)
    assert issubclass(PluginInstallError, ValueError)
    assert issubclass(PluginDependencyError, PluginLoadError)


def test_str_output_unchanged():
    """str(e) 与构造入参一致（消息格式是日志与既有测试的依赖）"""
    assert str(MarketError("索引拉取失败")) == "索引拉取失败"
    assert str(PluginDependencyError("插件循环依赖: a -> b")) == "插件循环依赖: a -> b"
    assert str(EventContractError("缺字段")) == "缺字段"


def test_telegram_error_keeps_extra_fields():
    """TelegramAPIError 自定义 __init__ 的 error_code/description 保持"""
    err = TelegramNotFoundError("not found", error_code=404, description="chat not found")
    assert err.error_code == 404
    assert err.description == "chat not found"
    assert str(err) == "not found"


def test_error_codes_are_unique():
    """每个错误类的 code 互不相同（WebUI 按码分类的前提）"""
    codes = {
        EventContractError.code,
        TelegramAPIError.code,
        HtmlRenderError.code,
        HtmlRenderUnavailableError.code,
        HtmlRenderTimeoutError.code,
        MarketError.code,
        PluginLoadError.code,
        PluginDependencyError.code,
        PluginInstallError.code,
    }
    assert len(codes) == 9
    assert AppError.code == "app_error"


def test_to_dict_payload():
    """to_dict 输出 code/message（detail 存在时附带）"""
    payload = PluginInstallError("归档解压后过大，已拒绝").to_dict()
    assert payload == {"code": "plugin_install_error", "message": "归档解压后过大，已拒绝"}

    payload = AppError("boom", detail="stack-ish").to_dict()
    assert payload["detail"] == "stack-ish"


def test_market_error_reexport_is_same_class():
    """market.py re-export 与 errors.py 是同一个类（唯一定义原则）"""
    assert MarketErrorReexport is MarketError


def test_html_render_error_reexport_is_same_class():
    """html_renderer.py re-export 与 errors.py 是同一个类"""
    from bot.html_renderer import HtmlRenderError as Reexported

    assert Reexported is HtmlRenderError


def test_app_error_without_message():
    """AppError 无消息时可用默认 str（不抛错）"""
    err = AppError()
    assert isinstance(err, Exception)


def test_manager_raises_typed_dependency_error():
    """管理器依赖解析抛 PluginDependencyError（继承 ValueError，存量捕获兼容）"""
    import asyncio
    import types

    from bot.plugin.manager import PluginManager

    plugin = types.SimpleNamespace(name="main", version="1.0.0", require=["dep>=2.0.0"])
    mgr = PluginManager()

    # 依赖 dep 未注册且模块不存在 → 依赖缺失
    with pytest.raises(PluginDependencyError, match="dep"):
        asyncio.run(mgr._ensure_dependencies(plugin, types.SimpleNamespace(), set()))
