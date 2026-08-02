"""FastAPI 接口层"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from bot.core.bot import get_bot as _get_bot
from bot.core.broadcast import register_broker


def get_bot():
    try:
        return _get_bot()
    except RuntimeError:
        return None

logger = logging.getLogger("qingci-bot.api")

# WebSocket 连接池（用于实时消息推送）
_ws_clients: set[WebSocket] = set()


async def _broadcast_message_to_ws(message: dict):
    """通过 WebSocket 广播消息"""
    if not _ws_clients:
        return
    data = json.dumps(message, ensure_ascii=False)
    disconnected = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.add(ws)
    _ws_clients -= disconnected


register_broker(_broadcast_message_to_ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info("API 服务启动")
    yield
    logger.info("API 服务关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="Qingci-Bot API", version="0.1.0", lifespan=lifespan)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    from api.routes import bot_router, config_router, plugin_router, log_router
    app.include_router(bot_router, prefix="/api/bot", tags=["Bot"])
    app.include_router(config_router, prefix="/api/config", tags=["Config"])
    app.include_router(plugin_router, prefix="/api/plugin", tags=["Plugin"])
    app.include_router(log_router, prefix="/api/log", tags=["Log"])

    # WebSocket 实时日志
    @app.websocket("/api/ws/log")
    async def ws_log(ws: WebSocket):
        await ws.accept()
        _ws_clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
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


async def broadcast_log(level: str, message: str):
    """向所有 WebSocket 客户端广播日志"""
    if not _ws_clients:
        return
    data = json.dumps({"level": level, "message": message, "type": "log"}, ensure_ascii=False)
    disconnected = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.add(ws)
    _ws_clients -= disconnected