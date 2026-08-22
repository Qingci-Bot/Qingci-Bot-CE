"""内置帮助插件测试：渲染成图片（方案 F）+ 文本兜底（A/C）+ hidden 过滤 + 别名"""

import tempfile
import time
from pathlib import Path

from bot.plugin.builtin.help import HelpPlugin
from bot.plugin.matcher import Matcher, MatcherContext


class _FakeRenderer:
    """模拟 html_renderer：记录渲染调用并产出临时 PNG"""

    def __init__(self):
        self.calls: list[dict] = []

    def is_supported(self) -> bool:
        return True

    async def render_html(self, html, **kwargs):
        self.calls.append({"html": html, **kwargs})
        path = Path(tempfile.gettempdir()) / f"help-test-{time.time_ns()}.png"
        path.write_bytes(b"fakepng")
        return path


async def _load_help(bot) -> HelpPlugin:
    await bot.plugin_manager.load_builtin(bot)
    return bot.plugin_manager.get("help")


def _ctx(args: str = "") -> MatcherContext:
    return MatcherContext(raw_event={"platform": "onebot"}, args=args)


async def test_help_falls_back_to_text_without_renderer(bot):
    """渲染能力不可用（TestBot 默认无 html_renderer）→ 回退纯文本，不开天窗"""
    plugin = await _load_help(bot)
    assert bot.html_renderer is None

    result = await plugin._cmd_help(_ctx())
    assert isinstance(result, str)
    assert "可用命令" in result
    assert "/help" in result


async def test_help_filter_by_target(bot):
    """/help <目标> 按插件名/命令名筛选（方案 A）"""
    plugin = await _load_help(bot)
    result = await plugin._cmd_help(_ctx("help"))
    assert isinstance(result, str)
    assert "/help" in result
    # 未命中的命令不应出现（如 chat 插件的对话命令不在 help 列表）
    assert "与「help」相关" not in result

    # 无命中 → 提示未找到
    missing = await plugin._cmd_help(_ctx("不存在的插件xyz"))
    assert isinstance(missing, str)
    assert "未找到" in missing


async def test_help_renders_image_segments(bot):
    """渲染能力可用 → 返回 image 消息段列表（dispatcher 原样透传）"""
    plugin = await _load_help(bot)
    fake = _FakeRenderer()
    bot.html_renderer = fake

    result = await plugin._cmd_help(_ctx())
    assert isinstance(result, list) and result
    assert fake.calls, "应实际调用 render_html"
    for seg in result:
        assert seg["type"] == "image"
        assert "file" in seg["data"]
    # HTML 卡片含命令与描述，且命令名已 escape
    html = fake.calls[0]["html"]
    assert "/help" in html
    assert "background" in html  # 卡片内联样式


async def test_help_skips_hidden_commands(bot, monkeypatch):
    """hidden_in_help 命令不出现在帮助列表（方案 E）"""
    plugin = await _load_help(bot)

    async def handler(ctx):
        return "x"

    visible = Matcher(handler=handler, event_type="message")
    visible.owner = "demo"
    visible.meta["command"] = "visible_cmd"
    hidden = Matcher(handler=handler, event_type="message")
    hidden.owner = "demo"
    hidden.meta["command"] = "hidden_cmd"
    hidden.meta["hidden_in_help"] = True

    monkeypatch.setattr(plugin.bot.plugin_manager, "all_matchers", lambda: [visible, hidden])
    result = await plugin._cmd_help(_ctx())
    assert isinstance(result, str)
    assert "visible_cmd" in result
    assert "hidden_cmd" not in result


def test_build_help_text_aliases_and_desc():
    """文本兜底：别名与描述展示；无描述不输出后缀"""
    pc = {
        "demo": {
            "category": "tool",
            "description": "",
            "commands": [("ping", ["p", "pong"], "测试命令"), ("abc", [], "")],
        }
    }
    text = HelpPlugin._build_help_text(pc)
    assert "别名：p/pong" in text
    assert "/ping（别名：p/pong） - 测试命令" in text
    assert "/abc" in text


def test_build_help_text_folds_when_many():
    """命令行数超阈值 → 折叠为仅命令名（方案 C 兜底），带提示"""
    commands = [(f"cmd{i}", [], f"desc{i}") for i in range(50)]
    pc = {"demo": {"category": "tool", "description": "", "commands": commands}}
    text = HelpPlugin._build_help_text(pc)
    assert "/cmd0" in text
    assert "已折叠" in text
    assert "desc0" not in text  # 折叠后不带描述


def test_build_help_html_escapes_injection():
    """HTML 卡片：命令名/描述/插件名一律 escape，防注入"""
    chunk = [("demo", {"category": "", "description": "", "commands": [("a<b", [], 'd>"')]})]
    html = HelpPlugin._build_help_html(chunk)
    assert "a&lt;b" in html
    assert "d&gt;" in html


def test_chunk_plugins_splits_oversize():
    """单插件命令过多 → 内部拆分为多份（同插件名），每份高度不超预算"""
    commands = [(f"cmd{i}", [], "") for i in range(200)]
    groups = [("huge", {"category": "", "description": "", "commands": commands})]
    chunks = HelpPlugin._chunk_plugins(groups)
    assert len(chunks) >= 2
    assert all(len(chunk) == 1 for chunk in chunks)
    total_rows = sum(len(info["commands"]) for c in chunks for _, info in c)
    assert total_rows == 200


async def test_help_renders_multiple_images_when_large(bot):
    """命令总量大 → 渲染多张图（按高度预算拆图）"""
    plugin = await _load_help(bot)
    fake = _FakeRenderer()
    bot.html_renderer = fake

    pc = {}
    for i in range(6):
        pc[f"plugin{i}"] = {
            "category": "tool",
            "description": "",
            "commands": [(f"p{i}_cmd{j}", [], "") for j in range(30)],
        }
    result = await plugin._try_render_help_image(pc)
    assert isinstance(result, list)
    assert len(fake.calls) >= 2  # 拆成多张图
    assert len(result) == len(fake.calls)


def test_schedule_cleanup_without_loop():
    """无运行事件循环时延迟清理静默跳过（不抛异常）"""
    # 在同步上下文调用：asyncio.get_running_loop() 抛 RuntimeError → pass
    HelpPlugin._schedule_cleanup(Path("nonexistent"))


async def test_schedule_cleanup_with_loop(tmp_path):
    """有运行事件循环时注册延迟删除（不抛异常；300s 后由回调清理）"""
    path = tmp_path / "img.png"
    path.write_bytes(b"x")
    HelpPlugin._schedule_cleanup(path)
    assert path.is_file()  # 延迟删除未立即生效
