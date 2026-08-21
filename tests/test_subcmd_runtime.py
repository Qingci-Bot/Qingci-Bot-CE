"""on_load 运行时注册的 on_command 子指令端到端验证（对应 shiguang 子命令场景）"""


async def test_on_load_subcommand_matchers_registered_and_dispatched(bot):
    """on_load 内注册的 on_command(subcommands)：子 matcher 随 parent 展开并正确路由"""
    await bot.load_plugin("plugin_pkg.subcmd_runtime_plugin")
    plugin = bot.plugin_manager.get("subcmd_runtime")
    assert plugin is not None
    # parent + 2 个子 matcher 全部注册
    assert len(plugin.matchers) == 3
    sub_commands = {m.meta.get("command") for m in plugin.matchers}
    assert {"排行", "排行 今日", "排行 月榜"} <= sub_commands

    # 父指令（无子指令参数）不拦截子指令消息；父指令自身返回 None 无回复
    assert await bot.send_private("/排行") is None
    # 子指令路由到对应 handler
    assert await bot.send_private("/排行 今日") == "今日排行"
    assert await bot.send_private("/排行 月榜") == "月榜排行"
