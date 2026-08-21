"""P1 功能测试：事件总线 / LLM 工具 / 指令系统增强 / 配置 schema

验证五项 P1 能力已真正接线：
1. 事件总线发布-订阅（含跨插件广播与通配订阅）
2. 插件级 LLM 工具声明（@llm_tool 注册到全局 ToolRegistry）
3. 指令系统增强（别名 / 类型化参数 / 子指令）
4. 配置 schema 自动生成（Config 内嵌类 → JSON Schema）
5. 事件总线可直接在 handler 中 publish 跨插件主题
"""


async def test_event_bus_subscribe_publish(bot):
    """插件 on_load 订阅事件，publish 后订阅者收到 data"""
    ok = await bot.load_plugin("plugin_pkg.p1_plugin")
    assert ok is True
    plugin = bot.plugin_manager.get("p1")
    assert plugin is not None

    await bot.event_bus.publish("p1.event", message="hi")
    assert plugin.received == {"message": "hi"}


async def test_event_bus_wildcard_subscription(bot):
    """订阅 "*" 接收所有事件"""
    got: list[tuple[str, dict]] = []

    async def handler(event_type: str, data: dict) -> None:
        got.append((event_type, data))

    await bot.event_bus.subscribe("*", handler)
    await bot.event_bus.publish("any.topic", x=1)
    assert got == [("any.topic", {"x": 1})]


async def test_event_bus_cross_plugin_broadcast(bot):
    """插件 A 在命令 handler 中 publish，插件 B 订阅接收（跨插件解耦）"""
    ok = await bot.load_plugin("plugin_pkg.p1_bus_consumer")
    assert ok is True
    ok = await bot.load_plugin("plugin_pkg.p1_plugin")
    assert ok is True

    reply = await bot.send_private("/broadcast hello-world")
    assert reply == "ok"

    consumer = bot.plugin_manager.get("p1_bus")
    assert consumer is not None
    assert consumer.received == {"text": "hello-world"}


async def test_command_aliases(bot):
    """命令别名触发（/cmd 与无斜杠形式均可）"""
    await bot.load_plugin("plugin_pkg.p1_plugin")
    assert await bot.send_private("天气 上海 2") == "上海:2天"


async def test_command_args_schema(bot):
    """args_schema 类型化解析：按空白切分并转换类型注入 handler 形参"""
    await bot.load_plugin("plugin_pkg.p1_plugin")
    assert await bot.send_private("/weather 北京 3") == "北京:3天"


async def test_command_subcommands(bot):
    """子指令路由：父指令含子指令时不拦截，子指令命中对应 handler"""
    await bot.load_plugin("plugin_pkg.p1_plugin")
    assert await bot.send_private("/admin") == "子指令: ban/unban"
    assert await bot.send_private("/admin ban alice") == "ban:alice"
    assert await bot.send_private("/admin unban bob") == "unban:bob"


async def test_llm_tool_declared_and_registered(bot):
    """@llm_tool 声明在插件加载时注册到全局 ToolRegistry（带插件名前缀）"""
    await bot.load_plugin("plugin_pkg.p1_plugin")
    assert bot.tool_registry.has("p1_tool_add") is True
    assert "p1_tool_add" in bot.plugin_manager._plugin_tools.get("p1", [])

    result = await bot.tool_registry.execute("p1_tool_add", {"a": 1, "b": 2})
    assert result == "3"


async def test_llm_tool_unregistered_on_unload(bot):
    """插件卸载时注销其声明的 LLM 工具"""
    await bot.load_plugin("plugin_pkg.p1_plugin")
    assert bot.tool_registry.has("p1_tool_add") is True
    await bot.plugin_manager.unload("p1")
    assert bot.tool_registry.has("p1_tool_add") is False


async def test_llm_tool_via_sdk_path_registered(bot):
    """插件从 qingci_plugin_sdk 导入 llm_tool（官方 hello 用法）时工具必须注册

    回归：CE 曾逐字复制 SDK 的 llm_tool 实现形成双收集栈，SDK 路径导入的
    工具进 SDK 收集栈、CE 收集栈为空，导致工具被静默丢弃。
    """
    await bot.load_plugin("plugin_pkg.sdk_llm_tool_plugin")
    assert bot.tool_registry.has("sdk_llm_tool_sdk_get_time") is True
    assert "sdk_llm_tool_sdk_get_time" in bot.plugin_manager._plugin_tools.get("sdk_llm_tool", [])

    result = await bot.tool_registry.execute("sdk_llm_tool_sdk_get_time", {})
    assert result == "12:00"


async def test_config_schema_generated(bot):
    """Config 内嵌 pydantic 类自动生成 JSON Schema（含类型与默认值）"""
    await bot.load_plugin("plugin_pkg.p1_plugin")
    schema = bot.plugin_manager.get_config_schema("p1")
    assert schema is not None
    assert schema["type"] == "object"
    props = schema["properties"]
    assert props["greeting"] == {"default": "你好", "title": "Greeting", "type": "string"}
    assert props["retries"]["type"] == "integer"
    assert props["verbose"]["type"] == "boolean"
