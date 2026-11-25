# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""首屏（first paint）契约：第一帧就必须是品牌暗色，不能白闪、不能空白

## 为什么值得单独立测试

样式表的 `<link>` 由 Vite 注入在 head 末尾，在它到达之前浏览器用**默认白色**
渲染文档。本项目是暗色主题，于是打开窗口会先闪一下白、再变暗——用户感知就是
"加载慢"，而真实原因是渲染顺序，不是性能。

修法是内联一段 critical CSS + 骨架。这类"修复"最容易被后续重构悄悄改回去
（换模板、换构建工具、有人觉得内联样式不优雅），而回归是**视觉的**：CI 不会
失败，只有用户看到白屏。所以用测试把三条约束钉住：

1. 内联样式里有暗色底 → 第一帧不会是白的
2. 骨架在 `#app` 内部 → Vue `mount('#app')` 时被整体替换，不需要清理逻辑
3. CSP 的 style-src 允许 inline → 否则内联样式会被拦掉，等于没写
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"
MAIN_JS = ROOT / "web" / "src" / "main.js"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _style_block() -> str:
    """取 head 里第一段内联 <style> 的内容"""
    m = re.search(r"<style>(.*?)</style>", _html(), re.S)
    assert m, "web/index.html 缺少内联 critical CSS"
    return m.group(1)


def test_inline_critical_css_sets_dark_background():
    """内联样式必须自带暗色底（不能只靠外部 CSS 文件）"""
    style = _style_block()
    assert "background" in style, "内联样式未设置背景"
    # 颜色值可能在构建时被压缩成无空格形式，这里只断言色值出现
    assert "0b0f1a" in style, "内联样式未使用主题底色 #0b0f1a"
    assert "color-scheme: dark" in style.replace("\n", " ").replace("  ", " ") or (
        "color-scheme:dark" in style.replace(" ", "")
    ), "缺少 color-scheme: dark（滚动条/表单控件会先闪白）"


def test_splash_lives_inside_app_mount_point():
    """骨架必须在 #app 内部，才能被 Vue 挂载时自动替换掉"""
    html = _html()
    app = re.search(r'<div id="app">(.*?)</div>\s*<script', html, re.S)
    assert app, "未找到 #app 容器"
    assert "boot-splash" in app.group(1), "首屏骨架不在 #app 内（挂载后不会消失）"

    # 与 main.js 的挂载点保持同一契约
    assert "mount('#app')" in MAIN_JS.read_text(encoding="utf-8")


def test_csp_allows_inline_style():
    """CSP 必须允许内联样式，否则 critical CSS 会被浏览器拦掉"""
    html = _html()
    m = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert m, "缺少 CSP meta"
    csp = m.group(1)
    style_src = re.search(r"style-src([^;]+)", csp)
    assert style_src, "CSP 未声明 style-src"
    assert "'unsafe-inline'" in style_src.group(1), (
        "style-src 未允许 'unsafe-inline'，内联首屏样式会被拦截"
    )


def test_splash_respects_reduced_motion():
    """尊重系统「减少动态效果」：动画必须可关闭

    首屏骨架带呼吸/滑动动画，对前庭敏感用户不友好；这是无障碍的基本要求。
    """
    assert "prefers-reduced-motion" in _style_block()
