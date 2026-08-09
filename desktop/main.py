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
    tray_ok = False
    # 真正退出标志（托盘「退出」触发），区别于点击关闭按钮（隐藏窗口）
    _exiting = False

    def _trigger_exit():
        """托盘退出回调：标记退出 + 关闭窗口"""
        nonlocal _exiting
        _exiting = True
        webview.destroy()

    try:
        # 提前验证托盘依赖可用，决定是否启用"关闭即驻留后台"行为
        import importlib.util
        if importlib.util.find_spec("pystray") is None:
            raise ImportError("pystray 未安装")

        from desktop.tray import SystemTray
        tray = SystemTray(
            on_show=lambda: (window.show(), window.restore()),
            on_exit=_trigger_exit,
        )
        tray_thread = threading.Thread(target=tray.create, daemon=True)
        tray_thread.start()
        tray_ok = True
    except Exception:
        logger.warning("系统托盘启动失败，关闭窗口将直接退出")

    if tray_ok:

        def _on_closing(*_args, **_kwargs):
            """点击关闭按钮 = 隐藏窗口驻留系统托盘，不退出进程。

            托盘「退出」触发时 _exiting=True，允许真正关闭窗口。
            返回 False 取消 pywebview 的关闭流程（WinForms 后端约定）；
            真正退出请用托盘右键菜单「退出」。
            """
            if _exiting:
                return True  # 真正退出，允许关闭
            try:
                window.hide()
            except Exception:
                logger.exception("隐藏窗口失败")
            return False

        window.events.closing += _on_closing

    webview.start(debug=False)

    # 窗口关闭后停止托盘
    if tray:
        tray.stop()