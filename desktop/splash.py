"""轻量启动画面 - 仅依赖 ctypes，零第三方库

使用 UpdateLayeredWindow 实现，不依赖 WM_PAINT / RegisterClassW，
在 PyInstaller 解压/Python 导入重型模块期间提供即时视觉反馈。
"""

import ctypes
import logging
import threading
from ctypes import wintypes

logger = logging.getLogger("qingci-bot.splash")

# ── Windows API 常量 ──────────────────────────────────────────
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
ULW_ALPHA = 0x00000002
SW_SHOW = 5

# 配色：暗色主题，与 Qingci-Bot CE UI 风格一致
BG_COLOR = 0x001E1E2E  # 深藏青 (BGR)
TITLE_COLOR = 0x00CDD6F4  # 亮薰衣草 (BGR) — 实际是 RGB(0xF4, 0xD6, 0xCD)
SUB_COLOR = 0x00A6ADC8  # 灰紫 (BGR)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


# ctypes.wintypes 未定义 BLENDFUNCTION，需手动声明（UpdateLayeredWindow 的混合参数）
class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    ]


class SplashScreen:
    """纯 ctypes 原生 Windows 启动画面（UpdateLayeredWindow 方案）

    用法:
        splash = SplashScreen()
        splash.show()          # 显示（非阻塞，后台线程）
        # ... 执行重型初始化 ...
        splash.close()         # 关闭
    """

    W, H = 360, 160

    def __init__(self):
        self._hwnd = None
        self._ready = threading.Event()
        self._running = True

    # ── 公开 API ──────────────────────────────────────────────

    def show(self):
        """在后台线程显示启动画面，等待窗口创建完成"""
        t = threading.Thread(target=self._run, name="splash", daemon=True)
        t.start()
        if not self._ready.wait(timeout=5):
            logger.warning("启动画面窗口创建超时（5s），继续启动")

    def close(self):
        """关闭启动画面（跨线程安全）"""
        self._running = False
        if self._hwnd:
            # PostMessage 跨线程安全；DefWindowProc 处理 WM_CLOSE 时
            # 会在窗口线程内调用 DestroyWindow，继而触发 WM_DESTROY →
            # PostQuitMessage → GetMessage 返回 0，消息循环退出
            user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
            self._hwnd = None

    # ── 内部实现 ──────────────────────────────────────────────

    def _run(self):
        try:
            self._create()
            self._ready.set()
            self._message_loop()
        except Exception:
            logger.exception("启动画面异常")
        finally:
            self._ready.set()

    def _create(self):
        """创建分层窗口并渲染内容"""
        # 1. 获取屏幕 DC
        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            raise OSError("GetDC 失败")

        try:
            # 2. 创建内存 DC 与兼容位图
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            if not hdc_mem:
                raise OSError("CreateCompatibleDC 失败")

            bitmap = gdi32.CreateCompatibleBitmap(hdc_screen, self.W, self.H)
            if not bitmap:
                gdi32.DeleteDC(hdc_mem)
                raise OSError("CreateCompatibleBitmap 失败")

            old_bitmap = gdi32.SelectObject(hdc_mem, bitmap)

            try:
                # 3. 绘制背景
                brush = gdi32.CreateSolidBrush(BG_COLOR)
                rect = wintypes.RECT(0, 0, self.W, self.H)
                user32.FillRect(hdc_mem, ctypes.byref(rect), brush)
                gdi32.DeleteObject(brush)

                gdi32.SetBkMode(hdc_mem, 1)  # TRANSPARENT

                # 4. 绘制标题
                gdi32.SetTextColor(hdc_mem, TITLE_COLOR)
                font_title = gdi32.CreateFontW(
                    38, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 0, 0, "Microsoft YaHei"
                )
                old_font = gdi32.SelectObject(hdc_mem, font_title)
                tr = wintypes.RECT(0, 28, self.W, 82)
                user32.DrawTextW(hdc_mem, "Qingci-Bot CE", -1, ctypes.byref(tr), 0x21)
                gdi32.SelectObject(hdc_mem, old_font)
                gdi32.DeleteObject(font_title)

                # 5. 绘制副标题
                gdi32.SetTextColor(hdc_mem, SUB_COLOR)
                font_sub = gdi32.CreateFontW(
                    20, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 0, 0, "Microsoft YaHei"
                )
                old_font = gdi32.SelectObject(hdc_mem, font_sub)
                sr = wintypes.RECT(0, 82, self.W, 118)
                user32.DrawTextW(hdc_mem, "正在启动...", -1, ctypes.byref(sr), 0x21)
                gdi32.SelectObject(hdc_mem, old_font)
                gdi32.DeleteObject(font_sub)

                # 6. 居中坐标
                sw = user32.GetSystemMetrics(0)
                sh = user32.GetSystemMetrics(1)
                x = (sw - self.W) // 2
                y = (sh - self.H) // 2

                # 7. 创建分层窗口（使用 Static 内置类，无需 RegisterClass）
                module = kernel32.GetModuleHandleW(None)
                self._hwnd = user32.CreateWindowExW(
                    WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
                    "Static",
                    "",
                    WS_POPUP,
                    x,
                    y,
                    self.W,
                    self.H,
                    None,
                    None,
                    module,
                    None,
                )
                if not self._hwnd:
                    raise OSError(f"CreateWindowExW 失败: {ctypes.get_last_error()}")

                # 8. UpdateLayeredWindow：将内存位图渲染到分层窗口
                pt_src = wintypes.POINT(0, 0)
                pt_dst = wintypes.POINT(x, y)
                size = wintypes.SIZE(self.W, self.H)
                blend = BLENDFUNCTION()
                blend.BlendOp = 0  # AC_SRC_OVER
                blend.BlendFlags = 0
                blend.SourceConstantAlpha = 240
                blend.AlphaFormat = 0  # 位图无 alpha 通道，用 SourceConstantAlpha 控制整体透明度

                user32.UpdateLayeredWindow(
                    self._hwnd,
                    hdc_screen,
                    ctypes.byref(pt_dst),
                    ctypes.byref(size),
                    hdc_mem,
                    ctypes.byref(pt_src),
                    0,
                    ctypes.byref(blend),
                    ULW_ALPHA,
                )

                user32.ShowWindow(self._hwnd, SW_SHOW)
            finally:
                gdi32.SelectObject(hdc_mem, old_bitmap)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(hdc_mem)
        finally:
            user32.ReleaseDC(None, hdc_screen)

    def _message_loop(self):
        """消息循环：保持窗口存活直到被关闭

        注意：必须先 Translate/Dispatch 再检查 self._running，
        否则 close() 置 _running=False 后 WM_CLOSE 永远不会被派发，
        窗口将残留屏幕。DispatchMessage 处理 WM_CLOSE → DefWindowProc →
        DestroyWindow → WM_DESTROY → PostQuitMessage → GetMessage 返回 0。
        """
        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break  # 收到 WM_QUIT 或错误，退出循环
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            if not self._running:
                # close() 已请求关闭：主动向窗口发送 WM_CLOSE 让其走销毁链
                if self._hwnd:
                    user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
                break
