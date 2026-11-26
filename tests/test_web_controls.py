# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""Web UI 控件契约：不许再用原生表单控件，窗口必须等比例缩放

## 为什么用静态检查而不是组件测试

前端目前没有 ``@vue/test-utils``（只有 vitest + jsdom），为一个约定引入组件
测试框架并不划算；而「有没有用原生控件」本身就是文本层面可判定的事实。
真正的视觉验证是在真实后端 + 无头浏览器上截图完成的（见
``.workbuddy/memory`` 里的记录），这里只负责防止回退。

## 背景

用户反馈「还有好多地方用的是原生组件」。原生 ``<select>`` / ``<input
type=checkbox>`` / ``<input type=range>`` 的外观由操作系统绘制：暗色页面上
会出现白底方框、亮色滑轨，且各平台高度不一致导致表单行高跳动。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_SRC = ROOT / "web" / "src"
FORM_DIR = WEB_SRC / "components" / "form"

NATIVE_CONTROL_PATTERNS = {
    "原生下拉 <select>": re.compile(r"<select\b"),
    "原生勾选框 type=checkbox": re.compile(r'type="checkbox"'),
    "原生滑杆 type=range": re.compile(r'type="range"'),
}


def _strip_comments(text: str) -> str:
    """剥掉 HTML 注释与脚本注释，保留换行以维持行号"""

    # HTML 注释（含多行）：整块替换为同等数量的换行
    def _blank_block(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    text = re.sub(r"<!--.*?-->", _blank_block, text, flags=re.S)
    # 单行 JS/TS 注释
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return text


def _vue_files() -> list[Path]:
    return sorted(WEB_SRC.rglob("*.vue"))


def test_no_native_form_controls_outside_form_components() -> None:
    """除 ``components/form/`` 自身（实现细节）外，任何 .vue 都不得使用原生控件"""
    offenders: list[str] = []
    for path in _vue_files():
        text = path.read_text(encoding="utf-8")
        # 先整体剥掉 HTML 注释块：组件文件顶部会用「替代原生 <select>」这类
        # 说明性文字，按行首判断过滤不掉块中间的行。剥注释后保留行数（用换行
        # 占位），报错行号才能对上源文件。
        scan_text = _strip_comments(text)
        for label, pattern in NATIVE_CONTROL_PATTERNS.items():
            for match in pattern.finditer(scan_text):
                line_no = scan_text[: match.start()].count("\n") + 1
                line = scan_text.split("\n")[line_no - 1]
                if line.lstrip().startswith(("--", "*", "//", "#")):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{line_no} {label}")
    assert not offenders, "仍有页面使用原生表单控件：\n" + "\n".join(offenders)


def test_form_components_exist_and_are_accessible() -> None:
    """四个替代组件存在，并带可访问性角色（键盘/读屏可用）"""
    required = {
        "AppSelect.vue": 'role="listbox"',
        "AppSwitch.vue": 'role="switch"',
        "AppSlider.vue": 'role="slider"',
        "AppCheckbox.vue": 'role="checkbox"',
    }
    for name, needle in required.items():
        path = FORM_DIR / name
        assert path.is_file(), f"缺少组件 {name}"
        assert needle in path.read_text(encoding="utf-8"), f"{name} 缺少 {needle}"


def test_select_supports_keyboard_and_outside_click() -> None:
    """下拉必须能用键盘（↑↓/Enter/Esc）操作，并支持点击外部关闭

    原生 select 的这些行为是免费的；自绘组件如果不补，等于用可访问性换了外观。
    """
    text = (FORM_DIR / "AppSelect.vue").read_text(encoding="utf-8")
    for key in ("ArrowDown", "ArrowUp", "Enter", "Escape"):
        assert key in text, f"AppSelect 未处理 {key}"
    assert "mousedown" in text and "contains" in text, "AppSelect 未实现点击外部关闭"


def test_slider_clamps_and_steps() -> None:
    """滑杆要按 step 取整并夹在 [min, max] 内，否则会写回越界值"""
    text = (FORM_DIR / "AppSlider.vue").read_text(encoding="utf-8")
    assert "clampToStep" in text
    assert "Math.min(props.max" in text or "Math.min(this.max" in text


def test_window_keeps_aspect_ratio() -> None:
    """桌面窗口必须等比例缩放

    Electron 只有 macOS/Windows 支持 ``setAspectRatio``；Linux 需要
    ``will-resize``/``resize`` 兜底，因此三层都应存在——只写其中一层会在
    某个平台上失效（用户拖一下就变形）。
    """
    main_js = ROOT / "desktop" / "electron" / "src" / "main.js"
    text = main_js.read_text(encoding="utf-8")

    assert "lockAspect" in text, "缺少窗口比例锁定函数"
    assert "setAspectRatio" in text, "缺少系统级比例约束（macOS/Windows）"
    assert "will-resize" in text, "缺少拖动中约束"
    assert 'win.on("resize"' in text or "win.on('resize'" in text, "缺少 Linux 兜底"
    # 最大化/全屏时不该被强行改回比例，否则用户无法真正全屏
    assert "isMaximized" in text and "isFullScreen" in text
