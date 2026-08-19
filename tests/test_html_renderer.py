"""HTML → 图片渲染服务测试

通过 monkeypatch 注入假 playwright，避免测试依赖真实浏览器安装；
同时覆盖「playwright 缺失 / render 关闭」两种不可用降级路径。
"""

import asyncio

import pytest

import bot.html_renderer as hrm
from bot.config import RenderConfig
from bot.html_renderer import (
    HtmlRenderer,
    HtmlRenderError,
    HtmlRenderTimeoutError,
    HtmlRenderUnavailableError,
)

# ──────────────────────────────────────────────────────────────────
# 假 playwright 对象（模拟 async_playwright().start() → chromium.launch()）
# ──────────────────────────────────────────────────────────────────


class FakePage:
    """假页面：记录渲染参数，screenshot 返回固定字节"""

    def __init__(self):
        self.set_content_args: tuple | None = None
        self.screenshot_kwargs: dict | None = None
        self.closed = False

    async def set_content(self, html, wait_until="load"):
        self.set_content_args = (html, wait_until)

    async def screenshot(self, **kwargs):
        self.screenshot_kwargs = kwargs
        return b"fake-image-bytes"

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.pages: list[FakePage] = []
        self.closed = False

    async def new_page(self, **kwargs):
        page = FakePage()
        page.new_page_kwargs = kwargs
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.browser = FakeBrowser()
        self.launch_kwargs: dict | None = None

    async def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakeAsyncPlaywright:
    """async_playwright 替代：start() 返回 FakePlaywright（类级注册表便于断言）"""

    instances: list[FakePlaywright] = []

    async def start(self):
        pw = FakePlaywright()
        type(self).instances.append(pw)
        return pw


# ──────────────────────────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────────────────────────


def make_renderer(**cfg) -> HtmlRenderer:
    return HtmlRenderer(RenderConfig(**cfg))


def install_fake_playwright(monkeypatch) -> type[FakeAsyncPlaywright]:
    """把 _import_async_playwright 替换为返回 FakeAsyncPlaywright 类的实现"""
    FakeAsyncPlaywright.instances = []
    monkeypatch.setattr(hrm, "_import_async_playwright", lambda: FakeAsyncPlaywright)
    return FakeAsyncPlaywright


# ──────────────────────────────────────────────────────────────────
# 不可用降级路径
# ──────────────────────────────────────────────────────────────────


async def test_render_disabled_raises_unavailable(tmp_path, monkeypatch):
    """render.enabled=False 时渲染直接不可用（即使 playwright 已安装）"""
    install_fake_playwright(monkeypatch)
    renderer = make_renderer(enabled=False)
    with pytest.raises(HtmlRenderUnavailableError, match="已关闭"):
        await renderer.render_html("<html>hi</html>", tmp_path / "a.jpg")


async def test_playwright_missing_probe_and_render(tmp_path, monkeypatch):
    """playwright 未安装：探测返回不可用并给出安装提示，渲染抛不可用错误"""
    monkeypatch.setattr(hrm, "_import_async_playwright", lambda: None)
    renderer = make_renderer()

    assert renderer.is_supported() is False

    result = await renderer.probe()
    assert result["available"] is False
    assert result["playwright_installed"] is False
    assert "playwright" in result["reason"]

    info = renderer.status_info()
    assert info["available"] is False
    assert info["checked"] is True

    with pytest.raises(HtmlRenderUnavailableError, match="playwright"):
        await renderer.render_html("<html>hi</html>", tmp_path / "a.jpg")


async def test_playwright_installed_but_browser_launch_fails(monkeypatch):
    """playwright 已安装但浏览器启动失败：探测标记不可用并给出原因"""
    install_fake_playwright(monkeypatch)

    async def _launch(self, **kwargs):
        raise RuntimeError("Executable doesn't exist")

    monkeypatch.setattr(FakeChromium, "launch", _launch)
    renderer = make_renderer()

    result = await renderer.probe()
    assert result["available"] is False
    assert "Executable" in result["reason"]
    # 启动失败后 playwright 句柄应被清理，避免资源泄漏
    assert renderer._playwright is None


# ──────────────────────────────────────────────────────────────────
# 正常渲染路径
# ──────────────────────────────────────────────────────────────────


async def test_render_writes_file_with_fake_playwright(tmp_path, monkeypatch):
    """成功渲染：写入指定路径，截图参数正确，浏览器复用"""
    fake = install_fake_playwright(monkeypatch)
    renderer = make_renderer()

    out = tmp_path / "nested" / "card.jpg"
    path = await renderer.render_html(
        "<html><body>你好</body></html>",
        out,
        width=960,
        height=540,
        image_format="jpeg",
        quality=80,
    )
    assert path == out
    assert path.read_bytes() == b"fake-image-bytes"

    pw = fake.instances[0]
    assert "--no-sandbox" in pw.chromium.launch_kwargs["args"]
    page = pw.chromium.browser.pages[0]
    assert page.new_page_kwargs["viewport"] == {"width": 960, "height": 540}
    assert page.screenshot_kwargs["type"] == "jpeg"
    assert page.screenshot_kwargs["quality"] == 80
    assert page.screenshot_kwargs["clip"] == {
        "x": 0,
        "y": 0,
        "width": 960,
        "height": 540,
    }
    # 注入禁用动画样式，保证渲染稳定
    assert "animation: none" in page.set_content_args[0]

    # 浏览器实例复用：再次渲染不再重新 launch
    await renderer.render_html("<html>b</html>", tmp_path / "b.jpg")
    assert len(fake.instances) == 1


async def test_render_png_temp_file_defaults(monkeypatch):
    """png 格式 + 省略输出路径：写入临时文件，screenshot 不传 quality"""
    install_fake_playwright(monkeypatch)
    renderer = make_renderer(format="png")

    path = await renderer.render_html("<html>hi</html>", width=100, height=80)
    assert path.suffix == ".png"
    assert path.exists()
    assert path.read_bytes() == b"fake-image-bytes"

    page = renderer._browser.pages[0]
    assert page.screenshot_kwargs["type"] == "png"
    assert "quality" not in page.screenshot_kwargs


async def test_render_scale_factor_from_config(monkeypatch):
    """device_scale_factor 默认取配置值，且可被调用方覆盖"""
    install_fake_playwright(monkeypatch)
    renderer = make_renderer(device_scale_factor=2.0)

    await renderer.render_html("<html>hi</html>")
    assert renderer._browser.pages[0].new_page_kwargs["device_scale_factor"] == 2.0

    await renderer.render_html("<html>hi</html>", width=10, height=10, device_scale_factor=1.0)
    assert renderer._browser.pages[1].new_page_kwargs["device_scale_factor"] == 1.0


# ──────────────────────────────────────────────────────────────────
# 超时与异常
# ──────────────────────────────────────────────────────────────────


async def test_render_timeout(tmp_path, monkeypatch):
    """渲染超时：抛 HtmlRenderTimeoutError 并关闭浏览器（下次自动重建）"""
    install_fake_playwright(monkeypatch)

    async def _hang(self, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(FakePage, "screenshot", _hang)
    renderer = make_renderer(timeout=0.05)

    with pytest.raises(HtmlRenderTimeoutError, match="超时"):
        await renderer.render_html("<html>hi</html>", tmp_path / "a.jpg")
    # 超时后浏览器被关闭，句柄清空
    assert renderer._browser is None
    assert renderer._playwright is None


async def test_render_error_rebuilds_browser(tmp_path, monkeypatch):
    """渲染异常：抛 HtmlRenderError（保留根因），关闭浏览器后可再次渲染"""
    fake = install_fake_playwright(monkeypatch)
    renderer = make_renderer()

    original_screenshot = FakePage.screenshot

    async def _boom(self, **kwargs):
        raise RuntimeError("page crashed")

    monkeypatch.setattr(FakePage, "screenshot", _boom)
    with pytest.raises(HtmlRenderError, match="渲染失败") as exc_info:
        await renderer.render_html("<html>hi</html>", tmp_path / "a.jpg")
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    # 浏览器已关闭、句柄清空
    assert renderer._browser is None
    assert renderer._playwright is None

    # 移除故障后，后续渲染自动重建浏览器成功
    monkeypatch.setattr(FakePage, "screenshot", original_screenshot)
    out = await renderer.render_html("<html>ok</html>", tmp_path / "ok.jpg")
    assert out.read_bytes() == b"fake-image-bytes"
    assert renderer._browser is not None
    assert len(fake.instances) == 2  # 第一次故障实例 + 重建实例


# ──────────────────────────────────────────────────────────────────
# 探测缓存与关闭
# ──────────────────────────────────────────────────────────────────


async def test_probe_cached_and_close(monkeypatch):
    """probe 结果缓存；close 幂等且清理浏览器与 playwright 句柄"""
    fake = install_fake_playwright(monkeypatch)
    renderer = make_renderer()

    first = await renderer.probe()
    second = await renderer.probe()
    assert first["available"] is True
    assert first == second  # 缓存命中，不重复探测
    assert len(fake.instances) == 1

    # 探测后浏览器已启动，可复用
    assert renderer._browser is not None

    await renderer.close()
    assert renderer._browser is None
    assert renderer._playwright is None
    assert fake.instances[0].chromium.browser.closed is True
    assert fake.instances[0].stopped is True

    # 幂等：重复 close 无副作用
    await renderer.close()


async def test_invalid_format_and_timeout_param(monkeypatch):
    """非法格式与非法 timeout 参数直接报错（不触碰 playwright）"""
    install_fake_playwright(monkeypatch)
    renderer = make_renderer()
    with pytest.raises(ValueError, match="格式"):
        await renderer.render_html("<html>hi</html>", image_format="webp")
    with pytest.raises(ValueError, match="timeout"):
        await renderer.render_html("<html>hi</html>", timeout=0)


# ──────────────────────────────────────────────────────────────────
# 浏览器生命周期并发安全（_browser_lock）
# ──────────────────────────────────────────────────────────────────


async def test_concurrent_ensure_browser_single_instance(monkeypatch):
    """并发触发浏览器启动：_browser_lock 保证只启动一次、复用同一实例"""
    fake = install_fake_playwright(monkeypatch)
    renderer = make_renderer()

    results = await asyncio.gather(*(renderer._ensure_browser() for _ in range(5)))
    # 所有并发调用返回同一浏览器实例，且底层只 launch 一次
    assert len({id(b) for b in results}) == 1
    assert len(fake.instances) == 1
    assert renderer._browser is results[0]


async def test_concurrent_close_idempotent(monkeypatch):
    """并发 close：互不踩踏，浏览器与 playwright 句柄全部清空"""
    fake = install_fake_playwright(monkeypatch)
    renderer = make_renderer()
    await renderer._ensure_browser()
    assert renderer._browser is not None

    await asyncio.gather(*(renderer.close() for _ in range(3)))
    assert renderer._browser is None
    assert renderer._playwright is None
    assert fake.instances[0].chromium.browser.closed is True
    assert fake.instances[0].stopped is True


async def test_concurrent_ensure_and_close_consistent(monkeypatch):
    """_ensure_browser 与 close 并发：不出现句柄覆盖，最终可重建"""
    install_fake_playwright(monkeypatch)
    renderer = make_renderer()

    async def _flap():
        await renderer._ensure_browser()
        await renderer.close()

    await asyncio.gather(*(_flap() for _ in range(4)))
    # 结束后句柄一致清空；再次渲染可正常重建
    assert renderer._browser is None
    assert renderer._playwright is None

    out = await renderer.render_html("<html>ok</html>")
    assert out.read_bytes() == b"fake-image-bytes"
    assert renderer._browser is not None
