"""P2 测试：细粒度事件处理钩子（run_preprocessor + on_calling_api）

验证：
1. run_preprocessor：Matcher 匹配成功后、handler 前触发；可拦截（返回非 None
   作为回复并停止分发）、放行（返回 None）、异常隔离、仅规则通过后触发。
2. on_calling_api：平台 API 调用前触发；可改写参数、阻止调用（抛异常）。
"""

import pytest


async def test_run_preprocessor_intercept(bot):
    """preprocessor 返回非 None 时拦截该 Matcher，返回值作为回复并停止分发"""
    await bot.load_plugin("plugin_pkg.p2_plugin")
    calls = []

    async def pre(bot, matcher, mctx):
        calls.append(1)
        return "已拦截"

    bot.add_matcher_preprocessor(pre)
    assert calls == []  # 注册后未触发
    assert await bot.send_private("/ping") == "已拦截"
    assert calls == [1]


async def test_run_preprocessor_pass_through(bot):
    """preprocessor 返回 None 时放行，handler 正常执行"""
    await bot.load_plugin("plugin_pkg.p2_plugin")
    bot.add_matcher_preprocessor(lambda bot, matcher, mctx: None)
    assert await bot.send_private("/ping") == "pong"


async def test_run_preprocessor_exception_isolated(bot):
    """preprocessor 抛异常被隔离，不影响 handler 与主链路"""
    await bot.load_plugin("plugin_pkg.p2_plugin")

    def bad(bot, matcher, mctx):
        raise RuntimeError("boom")

    bot.add_matcher_preprocessor(bad)
    assert await bot.send_private("/ping") == "pong"


async def test_run_preprocessor_only_after_match(bot):
    """preprocessor 仅在规则/权限匹配成功后触发；未命中 Matcher 不触发"""
    await bot.load_plugin("plugin_pkg.p2_plugin")
    calls = []

    async def pre(bot, matcher, mctx):
        calls.append(1)
        return None

    bot.add_matcher_preprocessor(pre)
    await bot.send_private("不存在的命令")
    assert calls == []


async def test_run_preprocessor_async_and_sync_mixed(bot):
    """同时支持 sync 与 async 钩子，全部放行"""
    await bot.load_plugin("plugin_pkg.p2_plugin")

    def sync_pre(bot, matcher, mctx):
        return None

    async def async_pre(bot, matcher, mctx):
        return None

    bot.add_matcher_preprocessor(sync_pre)
    bot.add_matcher_preprocessor(async_pre)
    assert await bot.send_private("/ping") == "pong"


async def test_on_calling_api_hook_modifies_params(bot):
    """on_calling_api 钩子返回新 params 时改写 API 调用参数"""
    await bot.load_plugin("plugin_pkg.p2_plugin")
    seen = []

    async def hook(api_name, params):
        seen.append(api_name)
        if api_name == "get_group_info":
            params["group_id"] = 999
            return params
        return None

    bot.register_api_hook(hook)
    assert await bot.send_private("/poke") == "done"
    assert seen == ["get_group_info"]
    recorded = [c for c in bot.api_calls if c[0] == "get_group_info"]
    assert recorded[0][1]["group_id"] == 999


async def test_on_calling_api_hook_blocks_call(bot):
    """on_calling_api 钩子抛异常时阻止该次 API 调用"""
    await bot.load_plugin("plugin_pkg.p2_plugin")

    def hook(api_name, params):
        if api_name == "group_ban":
            raise PermissionError("无权限执行 group_ban")

    bot.register_api_hook(hook)
    with pytest.raises(PermissionError):
        await bot.connection.call_api("group_ban", {"group_id": 1, "user_id": 2})
    # 被阻止的调用不进入 api_calls
    assert all(c[0] != "group_ban" for c in bot.api_calls)


async def test_on_calling_api_hook_pass_through(bot):
    """on_calling_api 钩子返回 None 时保持原参数"""
    await bot.load_plugin("plugin_pkg.p2_plugin")
    bot.register_api_hook(lambda api_name, params: None)
    assert await bot.send_private("/poke") == "done"
    called = [c for c in bot.api_calls if c[0] == "get_group_info"]
    assert called[0][1]["group_id"] == 20001
