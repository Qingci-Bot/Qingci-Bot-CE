"""HTML → 图片渲染服务（可选能力）

基于 Playwright 无头 Chromium 将 HTML 渲染为 JPEG/PNG 图片，供签到卡等
需要「HTML 模板 → 图片消息」的插件复用。

设计要点：
- playwright 为可选依赖（`pyproject.toml` 的 `[render]` 分组）。未安装、
  浏览器未下载或 `render.enabled` 关闭时渲染能力不可用：`render_html()`
  抛出 `HtmlRenderUnavailableError`，调用方应回退（如纯文本签到），
  框架启动不受影响。
- 浏览器实例惰性创建并复用（进程内单例），Bot 停止时经 `close()` 关闭；
  `close()` 幂等，从未使用过渲染器时为零开销。
- 能力探测 `probe()` 实际启动一次浏览器验证可用性并缓存结果，供
  `GET /api/bot/status` 的 `render` 字段与插件侧 `bot.html_renderer`
  查询；探测失败仅记日志，不阻断启动。
- 渲染统一 `wait_until="load"` 并注入禁用 CSS 动画/过渡的样式（与 T2I
  服务 `animations: disabled` 语义对齐）；截图范围即 viewport 区域，
  `device_scale_factor` 控制输出清晰度。
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .config import RenderConfig

logger = logging.getLogger("qingci-bot.core.html_renderer")

# 无头浏览器常用参数：--no-sandbox 兼容 Docker/root 环境，
# --disable-dev-shm-usage 规避容器 /dev/shm 过小导致的崩溃（Windows 上均无害）。
_BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

# 注入页面以禁用动画/过渡，保证渲染结果稳定可复现
_ANIMATION_DISABLE_STYLE = """
<style>
* { animation: none !important; transition: none !important; }
</style>
"""

# 输出文件临时文件前缀
_TEMP_FILE_PREFIX = "qingci-render-"


class HtmlRenderError(Exception):
    """HTML 渲染错误基类"""


class HtmlRenderUnavailableError(HtmlRenderError):
    """渲染能力不可用（playwright 未安装 / 浏览器缺失 / render.enabled 关闭）"""


class HtmlRenderTimeoutError(HtmlRenderError):
    """渲染超时"""


def _import_async_playwright() -> Any:
    """惰性导入 playwright.async_api.async_playwright（可选依赖，未安装返回 None）"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    return async_playwright


class HtmlRenderer:
    """HTML → 图片渲染服务（进程内单例，惰性启动浏览器）

    线程模型：浏览器/页面操作均为 asyncio 协程，事件循环内串行使用；
    并发渲染由调用方自行限流（本项目事件处理已受并发信号量约束）。
    """

    def __init__(self, config: RenderConfig):
        self._config = config
        self._playwright: Any = None
        self._browser: Any = None
        self._probe: dict[str, Any] | None = None
        self._probe_lock = asyncio.Lock()

    # ============ 配置 / 状态 ============

    @property
    def config(self) -> RenderConfig:
        return self._config

    def is_supported(self) -> bool:
        """playwright 包是否可导入（快检，不启动浏览器）"""
        return _import_async_playwright() is not None

    def status_info(self) -> dict[str, Any]:
        """同步状态快照（供 /api/bot/status 与插件查询）"""
        info: dict[str, Any] = {
            "enabled": bool(self._config.enabled),
            "supported": self.is_supported(),
            "available": False,
            "reason": "",
            "checked": False,
        }
        if self._probe is not None:
            info["available"] = bool(self._probe.get("available"))
            info["reason"] = str(self._probe.get("reason") or "")
            info["checked"] = True
        return info

    async def probe(self) -> dict[str, Any]:
        """实际探测渲染能力（启动一次浏览器验证），结果缓存供状态查询"""
        async with self._probe_lock:
            if self._probe is not None:
                return dict(self._probe)
            result: dict[str, Any] = {
                "playwright_installed": self.is_supported(),
                "available": False,
                "reason": "",
                "checked": True,
            }
            if not self.is_supported():
                result["reason"] = (
                    "playwright 未安装，可用 `uv pip install 'qingci-bot-ce[render]'` "
                    "与 `playwright install chromium` 启用 HTML 渲染"
                )
            else:
                try:
                    await self._ensure_browser()
                    result["available"] = True
                except Exception as exc:
                    result["reason"] = f"{type(exc).__name__}: {exc}"
                    logger.warning(f"HTML 渲染能力探测失败: {result['reason']}")
            self._probe = result
            return dict(result)

    # ============ 渲染 ============

    async def render_html(
        self,
        html: str,
        output_path: str | Path | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        image_format: str | None = None,
        quality: int | None = None,
        device_scale_factor: float | None = None,
        timeout: float | None = None,
    ) -> Path:
        """渲染 HTML 为图片并写入文件，返回文件路径

        - 输出格式由 image_format 决定（jpeg / png，默认取配置 render.format）
        - output_path 省略时写入临时文件（调用方负责清理）
        - 能力不可用时抛出 HtmlRenderUnavailableError，调用方应回退
        """
        if not self._config.enabled:
            raise HtmlRenderUnavailableError("render.enabled 已关闭，HTML 渲染不可用")
        fmt = (image_format or self._config.format or "jpeg").lower()
        if fmt not in ("jpeg", "png"):
            raise ValueError(f"不支持的图片格式: {fmt}")
        width = width or self._config.default_width
        height = height or self._config.default_height
        quality = quality if quality is not None else self._config.quality
        scale = (
            device_scale_factor
            if device_scale_factor is not None
            else self._config.device_scale_factor
        )
        timeout = timeout if timeout is not None else self._config.timeout
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")

        try:
            browser = await self._ensure_browser()
            return await asyncio.wait_for(
                self._render_once(
                    browser,
                    html,
                    output_path=output_path,
                    fmt=fmt,
                    width=width,
                    height=height,
                    quality=quality,
                    scale=scale,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # 超时取消可能让浏览器处于不稳定状态，关闭后下次自动重建
            logger.warning(f"HTML 渲染超时（>{timeout}s），将重建浏览器实例")
            await self.close()
            raise HtmlRenderTimeoutError(f"HTML 渲染超时（>{timeout}s）") from None
        except HtmlRenderError:
            raise
        except Exception as exc:
            logger.exception("HTML 渲染失败")
            await self.close()
            raise HtmlRenderError(f"HTML 渲染失败: {type(exc).__name__}: {exc}") from exc

    async def close(self) -> None:
        """关闭浏览器与 playwright 句柄（幂等；未使用过渲染器时零开销）"""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("关闭渲染浏览器失败（忽略）", exc_info=True)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("停止 playwright 失败（忽略）", exc_info=True)
            self._playwright = None

    # ============ 内部实现 ============

    async def _ensure_browser(self) -> Any:
        """惰性启动并复用无头浏览器"""
        if self._browser is not None:
            return self._browser
        async_playwright = _import_async_playwright()
        if async_playwright is None:
            raise HtmlRenderUnavailableError(
                "playwright 未安装，可用 `uv pip install 'qingci-bot-ce[render]'` "
                "与 `playwright install chromium` 启用 HTML 渲染"
            )
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(args=_BROWSER_ARGS)
        except Exception:
            # 启动失败时清理已启动的 playwright 句柄，避免资源泄漏
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
            raise
        return self._browser

    async def _render_once(
        self,
        browser: Any,
        html: str,
        *,
        output_path: str | Path | None,
        fmt: str,
        width: int,
        height: int,
        quality: int,
        scale: float,
    ) -> Path:
        """在指定浏览器上完成一次渲染（页面生命周期在此方法内闭环）"""
        page = await browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
        )
        try:
            await page.set_content(html + _ANIMATION_DISABLE_STYLE, wait_until="load")
            kwargs: dict[str, Any] = {
                "type": fmt,
                "full_page": False,
                "clip": {"x": 0, "y": 0, "width": width, "height": height},
            }
            if fmt == "jpeg":
                kwargs["quality"] = quality
            data = await page.screenshot(**kwargs)
        finally:
            try:
                await page.close()
            except Exception:
                pass
        return self._write_output(cast(bytes, data), output_path, fmt)

    @staticmethod
    def _write_output(data: bytes, output_path: str | Path | None, fmt: str) -> Path:
        """写入渲染产物：指定路径（自动建目录）或临时文件"""
        if output_path is None:
            suffix = ".jpg" if fmt == "jpeg" else ".png"
            fd, tmp = tempfile.mkstemp(suffix=suffix, prefix=_TEMP_FILE_PREFIX)
            os.close(fd)
            path = Path(tmp)
        else:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path
