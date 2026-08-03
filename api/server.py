"""FastAPI 接口层"""

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from bot.core.bot import get_bot as _get_bot, QingciBot
from bot.core.broadcast import register_broker
from api.auth import _get_configured_api_key


def get_bot() -> Optional[QingciBot]:
    try:
        return _get_bot()
    except RuntimeError:
        return None


logger = logging.getLogger("qingci-bot.api")

# WebSocket 连接池（用于实时消息推送）
_ws_clients: set[WebSocket] = set()
_MAX_WS_CLIENTS = 32


async def _send_to_all_ws(data: str) -> None:
    """向所有 WebSocket 客户端发送数据（并发）"""
    if not _ws_clients:
        return
    clients = list(_ws_clients)
    results = await asyncio.gather(
        *[ws.send_text(data) for ws in clients],
        return_exceptions=True,
    )
    for ws, result in zip(clients, results):
        if isinstance(result, Exception):
            _ws_clients.discard(ws)


async def _broadcast_message_to_ws(message: dict) -> None:
    """通过 WebSocket 广播消息"""
    await _send_to_all_ws(json.dumps(message, ensure_ascii=False))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info("API 服务启动")
    yield
    # 清理 WebSocket 连接
    for ws in list(_ws_clients):
        try:
            await ws.close()
        except Exception:
            pass
    _ws_clients.clear()
    logger.info("API 服务已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="Qingci-Bot API", version="0.1.0", lifespan=lifespan)

    # CORS：不使用 allow_credentials=True + allow_origins=["*"]（违反 CORS 规范）
    # 安全由 X-API-Key 鉴权保证，CORS 仅放开方法/头
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    from api.routes import bot_router, config_router, plugin_router, log_router
    app.include_router(bot_router, prefix="/api/bot", tags=["Bot"])
    app.include_router(config_router, prefix="/api/config", tags=["Config"])
    app.include_router(plugin_router, prefix="/api/plugin", tags=["Plugin"])
    app.include_router(log_router, prefix="/api/log", tags=["Log"])

    # 注册 WebSocket 广播 broker（register_broker 内部已去重，create_app 多次调用安全）
    register_broker(_broadcast_message_to_ws)

    # WebSocket 实时日志（鉴权：通过 token 查询参数传递 API Key）
    @app.websocket("/api/ws/log")
    async def ws_log(ws: WebSocket, token: str = Query(default="")):
        configured_key = _get_configured_api_key()
        if configured_key is None:
            # 配置读取失败，fail-closed
            await ws.close(code=4001, reason="服务暂不可用")
            return
        if configured_key and not secrets.compare_digest(token, configured_key):
            await ws.close(code=4001, reason="未授权")
            return
        # 连接数限制（accept 前检查）
        if len(_ws_clients) >= _MAX_WS_CLIENTS:
            await ws.close(code=4003, reason="连接数已满")
            return
        await ws.accept()
        _ws_clients.add(ws)
        # 二次检查，防止并发超过上限
        if len(_ws_clients) > _MAX_WS_CLIENTS:
            _ws_clients.discard(ws)
            await ws.close(code=4003, reason="连接数已满")
            return
        last_recv = time.monotonic()
        try:
            while True:
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=60)
                    last_recv = time.monotonic()
                except asyncio.TimeoutError:
                    # 60 秒无消息，发送 ping 再等 30 秒
                    if time.monotonic() - last_recv > 90:
                        # 超过 90 秒无任何消息，断开
                        break
                    try:
                        await ws.send_json({"type": "ping"})
                    except Exception:
                        break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("WebSocket 异常断开", exc_info=True)
        finally:
            _ws_clients.discard(ws)

    # 静态文件（Web UI 构建产物）
    import os
    web_dir = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
    if os.path.exists(web_dir):
        app.mount("/ui", StaticFiles(directory=web_dir, html=True), name="web")

        @app.get("/")
        async def root():
            return RedirectResponse(url="/ui")

    return app
