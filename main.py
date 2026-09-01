"""Qingci-Bot CE 统一入口

用法:
    python main.py                  # 启动 Bot + API 服务
    python main.py --no-bot         # 仅启动 API 服务（Web UI）
    python main.py --port 8080      # 指定 API 端口
    python main.py --backend        # 由 Electron 壳拉起的后端进程（仅供桌面壳使用）
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# ── 早期环境设置 ──────────────────────────────────────────────

# frozen windowed（console=False）兼容：此时 sys.stdout/stderr 为 None，
# 任何 print / logging 写入都会抛异常且不可见。在最早期（任何第三方导入之前）
# 兜底重定向到 os.devnull，一处修复覆盖所有 print/logging；console 模式不受影响。
if getattr(sys, "frozen", False) and sys.stdout is None:
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 跳过 litellm 启动时的远程 model cost map 拉取：无外网/慢网环境下
# httpx.get(timeout=5) 会因 DNS 解析失败阻塞数秒，显著拖慢启动。
# 直接使用包内本地备份（功能无影响），仅影响极个别新模型的计价信息。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

# frozen 打包（EXE）内置 Playwright 浏览器：优先使用产物目录下
# ms-playwright/ 中随包分发的浏览器，避免要求最终用户另行执行
# `playwright install chromium`。开发环境（非 frozen）不受影响，
# 仍走 playwright 默认的浏览器查找路径（用户缓存目录）。
if getattr(sys, "frozen", False):
    _bundled_browsers = Path(sys.executable).resolve().parent / "ms-playwright"
    if _bundled_browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_bundled_browsers))

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("qingci-bot.main")

# ── 轻量级模块级导入（仅路径/日志工具，不触发重型依赖）───────
from bot.logformat import apply_logging_from_config  # noqa: E402 — 仅 logging 工具
from bot.paths import app_root  # noqa: E402 — 仅 sys + pathlib，极轻量

# ── 命令行解析 ────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Qingci-Bot CE")
    parser.add_argument("--no-bot", action="store_true", help="仅启动 API 服务")
    parser.add_argument(
        "--backend",
        action="store_true",
        help="由 Electron 壳拉起的后端进程：仅运行 Bot+API，就绪时打印机器可读端口供主机发现",
    )
    parser.add_argument(
        "--resolve-instance",
        action="store_true",
        help="仅解析实例元数据并打印 JSON（data_dir/config/port/host/instance），不启动任何服务；供 Electron 壳预探测",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="API 端口（默认 8080；实例模式下取实例元数据端口）"
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="API 监听地址")
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="可写数据目录（DB/插件数据/日志等，多实例隔离；默认实例内 data/）",
    )
    parser.add_argument(
        "--instance",
        type=str,
        default=None,
        help="实例名（instances/<name>）；指定后 config.yaml/data/plugins 均取自该实例目录",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="配置文件路径（默认 <应用根>/config.yaml；指定 --instance 时默认取实例内 config.yaml）",
    )
    args = parser.parse_args()
    return args


# ── 后端服务（重型导入延迟到函数内）───────────────────────────


async def run_bot_and_api(args, ready_callback=None):
    """在同个事件循环中运行 Bot 和 API 服务

    ready_callback: 可选回调，API 服务端口确定并可用后调用
                    （Electron 壳用它发现后端地址）。
    """

    # 重型导入：仅在需要启动后端时才加载
    import uvicorn  # noqa: E402

    from api.auth import set_config_path  # noqa: E402
    from api.server import create_app  # noqa: E402
    from bot.core.bot import QingciBot, clear_bot, set_bot  # noqa: E402

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

        # 等待端口可用的通知任务，与 serve() 并行。
        # 不依赖 uvicorn 内部 started 标志（版本间不一致），改为对健康端点的
        # HTTP 探测——服务真实可服务才算就绪，更贴近 Electron 主机预期。
        if ready_callback is not None:
            import httpx  # noqa: E402

            async def _notify_when_ready():
                base = f"http://{args.host}:{args.port}"
                for _ in range(600):  # 最多等 60s
                    try:
                        # 免鉴权专用健康检查端点；探测成功即视为后端就绪
                        r = await httpx.AsyncClient(timeout=1).get(f"{base}/api/bot/health")
                        if r.status_code < 500:
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)
                ready_callback(port=args.port)

            asyncio.create_task(_notify_when_ready())

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
        logger.info("Qingci-Bot CE 已停止")


# ── 入口 ──────────────────────────────────────────────────────


def main():
    # 跨进程重启助手模式：桌面/CLI 切换实例、运行中实例改名时，由分离的助手
    # 进程等待旧进程退出后重新拉起。必须在任何重型逻辑（实例解析/单实例保护/
    # 启动画面）之前处理，命中即退出本进程。
    from desktop.py.relaunch import run_helper_if_requested

    if run_helper_if_requested():
        return

    args = parse_args()

    from bot.paths import set_data_root, set_desktop_flag, set_plugins_dir

    # 桌面壳标识：由 Electron 以 --backend 拉起时置位，供切换/重启实例时
    # 决定由 Electron 抑或 detached 助手接手（见 api/routes/instances.py）
    set_desktop_flag(bool(args.backend))

    # 启动必须绑定实例（无全局模式）：--instance 显式指定，否则自动选择默认实例
    # （default 优先，其次名称排序第一个）；实例数为 0 时自动创建 default，确保至少一个。
    from bot.instances import ensure_default_instance, get_instance, instance_path

    if args.instance is None:
        inst = ensure_default_instance()
        args.instance = inst.name
        logger.info("未指定实例，自动启动到默认实例 %r", inst.name)
    else:
        inst = get_instance(args.instance)
        if inst is None:
            logger.error(f"实例不存在: {args.instance}")
            sys.exit(1)

    # 实例目录一次性决定 config/data_root/plugins/port 四个维度（完全自包含目录）。
    # 显式 --config/--data-dir/--port 优先级更高，可覆盖实例推导值。
    inst_dir = instance_path(args.instance)
    if args.data_dir is None:
        args.data_dir = str(inst_dir / "data")
    if args.config is None:
        args.config = str(inst_dir / "config.yaml")
    if args.port is None:
        args.port = inst.port
    set_plugins_dir(inst_dir / "plugins")

    # 解析实例数据根，先于任何数据访问（DB/日志/插件数据）
    data_dir = Path(args.data_dir).resolve() if args.data_dir else app_root() / "data"
    set_data_root(data_dir)
    if args.config is None:
        args.config = str(app_root() / "config.yaml")
    if args.port is None:
        args.port = 8080

    # Electron 壳预探测模式：仅输出实例元数据（与真实启动完全一致的推导结果），
    # 供 Electron 主进程在 spawn 后端前定位 data_dir、端口、前后端进程的启动参数。
    if args.resolve_instance:
        import json  # noqa: E402

        print(
            json.dumps(
                {
                    "instance": args.instance,
                    "data_dir": str(data_dir),
                    "config": args.config,
                    "port": args.port,
                    "host": args.host,
                }
            ),
            flush=True,
        )
        return

    # 单实例保护：按数据根派生互斥名——同一实例重复双击聚焦已有窗口；
    # 不同实例（不同 --data-dir）互不阻塞，可多开。
    # Electron 后端模式（--backend）由 Electron 壳负责单实例/聚焦，此处跳过互斥。
    from desktop.py.single_instance import (  # noqa: E402
        SingleInstance,
        mutex_name_for_data_dir,
    )

    if args.backend:
        _instance = None
    else:
        _instance = SingleInstance(name=mutex_name_for_data_dir(data_dir))
        if not _instance.acquire():
            logger.info("已有实例正在运行，本次启动退出")
            return

    # 结构化日志：config.bot.log_json=True 时切换 JSON 格式
    apply_logging_from_config(args.config)

    # Electron 后端模式（--backend）：仅运行 Bot+API，不创建任何窗口/托盘，
    # 就绪时打印 "\x1eQINGCI_READY <port>\x1e" 供 Electron 主机解析（RS 分隔，
    # 避免与普通日志行混淆）。退出时由 Electron 壳负责终止本进程。
    if args.backend:

        def _on_ready(*, port):
            print(f"\x1eQINGCI_READY {port}\x1e", flush=True)

        try:
            asyncio.run(run_bot_and_api(args, ready_callback=_on_ready))
        except KeyboardInterrupt:
            logger.info("收到退出信号")
        except asyncio.CancelledError:
            logger.warning("运行被取消")
        except Exception:
            logger.exception("运行异常")
            sys.exit(1)
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
