"""Qingci-Bot 统一入口

用法:
    python main.py                  # 启动 Bot + API 服务
    python main.py --no-bot         # 仅启动 API 服务（Web UI）
    python main.py --desktop        # 启动桌面应用
    python main.py --port 8080      # 指定 API 端口
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# frozen windowed（console=False）兼容：此时 sys.stdout/stderr 为 None，
# 任何 print / logging 写入都会抛异常且不可见。在最早期（任何第三方导入之前）
# 兜底重定向到 os.devnull，一处修复覆盖所有 print/logging；console 模式不受影响。
if getattr(sys, "frozen", False) and sys.stdout is None:
    import os

    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 跳过 litellm 启动时的远程 model cost map 拉取：无外网/慢网环境下
# httpx.get(timeout=5) 会因 DNS 解析失败阻塞数秒，显著拖慢启动。
# 直接使用包内本地备份（功能无影响），仅影响极个别新模型的计价信息。
import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

import uvicorn

from bot.core.bot import QingciBot, set_bot, clear_bot
from bot.core.logformat import apply_logging_from_config
from bot.paths import app_root
from api.auth import set_config_path
from api.server import create_app

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("qingci-bot.main")


def parse_args():
    parser = argparse.ArgumentParser(description="Qingci-Bot")
    parser.add_argument("--no-bot", action="store_true", help="仅启动 API 服务")
    parser.add_argument("--desktop", action="store_true", help="启动桌面应用")
    parser.add_argument("--port", type=int, default=8080, help="API 端口")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="API 监听地址")
    parser.add_argument("--config", type=str, default=str(app_root() / "config.yaml"), help="配置文件路径")
    args = parser.parse_args()
    # UX：frozen windowed 下双击（无任何参数）没有控制台也没有窗口，
    # 用户感知为"点了没反应"；此时默认启用桌面模式提供可见窗口。
    # 显式传参（--no-bot/--port 等）时保持原行为不变。
    if getattr(sys, "frozen", False) and len(sys.argv) <= 1 and not args.desktop:
        args.desktop = True
    return args


async def run_bot_and_api(args):
    """在同个事件循环中运行 Bot 和 API 服务"""
    bot = QingciBot(args.config)
    set_bot(bot)
    server = None

    try:
        if not args.no_bot:
            await bot.start()

        set_config_path(Path(args.config))
        app = create_app()
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
            access_log=True,
        )
        server = uvicorn.Server(config)

        logger.info(f"Web UI: http://{args.host}:{args.port}/ui")
        await server.serve()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("服务取消，开始清理...")
        if server:
            server.should_exit = True
        raise
    finally:
        if server:
            try:
                if server.started:
                    server.should_exit = True
                    await asyncio.wait_for(server.shutdown(), timeout=3)
            except (Exception, asyncio.CancelledError):
                logger.exception("uvicorn 关闭异常")
                server.force_exit = True

        if bot:
            try:
                await bot.stop()
            except (Exception, asyncio.CancelledError):
                logger.exception("Bot 停止异常")
        clear_bot()
        logger.info("Qingci-Bot 已停止")


def main():
    args = parse_args()

    # 结构化日志：config.bot.log_json=True 时切换 JSON 格式，否则保持上方文本格式不变
    apply_logging_from_config(args.config)

    if args.desktop:
        from desktop.main import run_desktop
        run_desktop(args)
        return

    try:
        asyncio.run(run_bot_and_api(args))
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    except asyncio.CancelledError:
        logger.warning("运行被取消")
    except Exception:
        logger.exception("运行异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
