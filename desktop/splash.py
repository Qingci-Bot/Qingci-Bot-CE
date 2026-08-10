"""轻量启动画面 - 仅依赖 ctypes，零第三方库

在 PyInstaller 解压/Python 导入重型模块期间显示，提供即时视觉反馈。
"""

import ctypes
import threading
from ctypes import wintypes

# ── Windows API 常量 ──────────────────────────────────────────
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
LWA_ALPHA = 0x00000002
WM_CLOSE = 0x0010
SW_SHOW = 5

# 配色：暗色主题，与 Qingci-Bot UI 风格一致
BG_COLOR = 0x001E1E2E       # 深藏青
TITLE_COLOR = 0x00CDD6F4    # 亮薰衣草
SUB_COLOR = 0x00A6ADC8      # 灰紫

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


class SplashScreen:
    """纯 ctypes 原生 Windows 启动画面

    用法:
        splash = SplashScreen()
        splash.show()          # 显示（非阻塞，后台线程）
        # ... 执行重型初始化 ...
        splash.close()         # 关闭
    """

    def __init__(self):
        self._hwnd = None
        self._class_atom = None
        self._ready = threading.Event()
        self._wndproc_ref = None  # 保持 WINFUNCTYPE 引用，防止 GC

    # ── 公开 API ──────────────────────────────────────────────

    def show(self):
        """在后台线程显示启动画面，等待窗口创建完成"""
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        self._ready.wait(timeout=3)

    def close(self):
        """关闭启动画面"""
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    # ── 内部实现 ──────────────────────────────────────────────

    def _run(self):
        self._register_class()
        self._create_window()
        self._ready.set()
        self._message_loop()

    def _register_class(self):
        module = kernel32.GetModuleHandleW(None)

        self._wndproc_ref = ctypes.WINFUNCTYPE(
            ctypes.c_long, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM
        )(self._wnd_proc)

        wc = wintypes.WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = module
        wc.hbrBackground = gdi32.CreateSolidBrush(BG_COLOR)
        wc.lpszClassName = "QingciBotSplash"
        self._class_atom = user32.RegisterClassW(ctypes.byref(wc))

    def _create_window(self):
        module = kernel32.GetModuleHandleW(None)

        # 居中
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        w, h = 360, 160
        x = (sw - w) // 2
        y = (sh - h) // 2

        self._hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            "QingciBotSplash",
            "",
            WS_POPUP,
            x, y, w, h,
            None, None, module, None,
        )

        user32.SetLayeredWindowAttributes(self._hwnd, 0, 240, LWA_ALPHA)
        user32.ShowWindow(self._hwnd, SW_SHOW)
        user32.UpdateWindow(self._hwnd)

    def _message_loop(self):
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == 0x000F:  # WM_PAINT
            self._on_paint(hwnd)
            return 0
        elif msg == 0x0002:  # WM_DESTROY
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_paint(self, hwnd):
        ps = wintypes.PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT

        # 标题
        gdi32.SetTextColor(hdc, TITLE_COLOR)
        font_title = gdi32.CreateFontW(
            38, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 0, 0, "Microsoft YaHei"
        )
        old = gdi32.SelectObject(hdc, font_title)
        tr = wintypes.RECT()
        tr.left, tr.top = rect.left, rect.top + 28
        tr.right, tr.bottom = rect.right, rect.top + 82
        user32.DrawTextW(hdc, "Qingci-Bot", -1, ctypes.byref(tr), 0x21)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font_title)

        # 副标题
        gdi32.SetTextColor(hdc, SUB_COLOR)
        font_sub = gdi32.CreateFontW(
            20, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 0, 0, "Microsoft YaHei"
        )
        old = gdi32.SelectObject(hdc, font_sub)
        sr = wintypes.RECT()
        sr.left, sr.top = rect.left, rect.top + 82
        sr.right, sr.bottom = rect.right, rect.top + 118
        user32.DrawTextW(hdc, "正在启动...", -1, ctypes.byref(sr), 0x21)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font_sub)

        user32.EndPaint(hwnd, ctypes.byref(ps))