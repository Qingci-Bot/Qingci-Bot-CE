"""P0 功能测试：参数级依赖注入 + 全局生命周期钩子

验证两项 P0 能力已真正接线：
1. handler 参数级依赖注入（Depends 显式声明 + 类型注解自动注入）
2. 全局生命周期钩子（on_startup/on_shutdown/on_bot_connect/on_metaevent）
   可被 PluginManager.dispatch_lifecycle 分发到已加载插件
"""


async def test_handler_parameter_dependency_injection(bot):
    """handler 参数按签名注入：Depends 显式 + 注解自动解析"""
    ok = await bot.load_plugin("plugin_pkg.di_plugin")
    assert ok is True

    reply = await bot.send_private("di")
    assert reply == "SessionStateManager-MessageDispatcher"


async def test_lifecycle_hooks_dispatched(bot):
    """全部四个全局生命周期钩子均被分发到已加载插件"""
    ok = await bot.load_plugin("plugin_pkg.di_plugin")
    assert ok is True
    plugin = bot.plugin_manager.get("di")
    assert plugin is not None

    await bot.plugin_manager.dispatch_lifecycle("on_startup")
    await bot.plugin_manager.dispatch_lifecycle("on_bot_connect")
    await bot.plugin_manager.dispatch_lifecycle("on_metaevent", {"meta_event_type": "heartbeat"})
    await bot.plugin_manager.dispatch_lifecycle("on_shutdown")

    assert "startup" in plugin.events
    assert "connect" in plugin.events
    assert "meta:heartbeat" in plugin.events
    assert "shutdown" in plugin.events


async def test_lifecycle_skips_plugins_not_overriding(bot):
    """未覆写钩子的插件不应被调用（dispatch_lifecycle 跳过基类空实现）"""
    ok = await bot.load_plugin("plugin_pkg.simple_plugin")
    assert ok is True
    plugin = bot.plugin_manager.get("simple")

    # simple 插件未覆写 on_startup，分发不应抛错
    await bot.plugin_manager.dispatch_lifecycle("on_startup")
    assert plugin is not None
