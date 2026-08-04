"""系统托盘模块"""

import logging
from pathlib import Path

logger = logging.getLogger("qingci-bot.desktop.tray")


class SystemTray:
    """系统托盘图标"""

    def __init__(self, on_show=None, on_exit=None):
        self._on_show = on_show
        self._on_exit = on_exit
        self._icon = None

    def create(self):
        """创建托盘图标"""
        try:
            import pystray
            from PIL import Image

            # 生成默认图标（32x32 蓝色圆形）
            # 源码模式位于 desktop/ 目录；frozen 模式写到 exe 所在目录（不写回包内）
            import sys
            if getattr(sys, "frozen", False):
                icon_path = Path(sys.executable).resolve().parent / "tray-icon.png"
            else:
                icon_path = Path(__file__).parent / "icon.png"
            if not icon_path.exists():
                self._generate_default_icon(icon_path)

            image = Image.open(str(icon_path))

            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", self._show_window, default=True),
                pystray.MenuItem("退出", self._exit),
            )

            self._icon = pystray.Icon("qingci-bot", image, "Qingci-Bot", menu)
            self._icon.run()
        except ImportError:
            logger.warning("pystray 未安装，跳过托盘图标")

    def stop(self):
        if self._icon:
            self._icon.stop()

    def _show_window(self):
        if self._on_show:
            self._on_show()

    def _exit(self):
        if self._on_exit:
            self._on_exit()
        self.stop()

    def _generate_default_icon(self, path: Path):
        """生成默认图标"""
        from PIL import Image, ImageDraw
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 蓝色圆形背景
        draw.ellipse([4, 4, size - 4, size - 4], fill=(66, 133, 244, 255))
        # 白色文字 "Q"
        draw.text((size // 2 - 8, size // 2 - 14), "Q", fill=(255, 255, 255, 255))
        img.save(path, "PNG")