"""桌面应用入口 - PyWebView + 系统托盘"""

import asyncio
import logging
import threading
import time

logger = logging.getLogger("qingci-bot.desktop")


def run_desktop(args):
    """启动桌面应用"""
    import webview

    # 结构化日志：与 main 入口保持一致（幂等：log_json=False 时不做任何变更）
    from bot.core.logformat import apply_logging_from_config
    apply_logging_from_config(args.config)

    # 在后台线程启动 Bot + API
    from main import run_bot_and_api

    def run_backend():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot_and_api(args))
        except Exception:
            logger.exception("后端服务异常")

    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()

    # 轮询等待 API 启动
    import httpx
    url = f"http://{args.host}:{args.port}"
    for _ in range(30):
        try:
            httpx.get(f"{url}/api/bot/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)

    # 桌面窗口直接加载 HTTP 服务下的 Web UI。
    # 注意：不能用 file:// 加载 web/dist/index.html —— Vite 产物 base=/ui/，
    # 资源引用为绝对路径 /ui/assets/...，file:// 协议下会解析到磁盘根目录而全部 404，
    # 表现为窗口空白。经 HTTP 由 FastAPI 静态服务提供资源。
    url = f"http://{args.host}:{args.port}/ui"

    # 创建窗口
    window = webview.create_window(
        title="Qingci-Bot",
        url=url,
        width=1100,
        height=750,
        min_size=(800, 600),
        resizable=True,
        fullscreen=False,
    )

    # 启动系统托盘
    tray = None
    try:
        from desktop.tray import SystemTray
        tray = SystemTray(
            on_show=lambda: window.restore() if window else None,
            on_exit=lambda: webview.destroy(),
        )
        tray_thread = threading.Thread(target=tray.create, daemon=True)
        tray_thread.start()
    except Exception:
        logger.warning("系统托盘启动失败")

    webview.start(debug=False)

    # 窗口关闭后停止托盘
    if tray:
        tray.stop()