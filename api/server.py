"""FastAPI 接口层"""

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from bot.core.bot import get_bot as _get_bot, QingciBot
from bot.core.broadcast import register_broker, unregister_broker
from api.auth import _get_configured_api_key


def get_bot() -> Optional[QingciBot]:
    try:
        return _get_bot()
    except RuntimeError:
        return None


logger = logging.getLogger("qingci-bot.api")

# WebSocket 连接池（实时消息推送）与对话调试台连接池
_ws_clients: set[WebSocket] = set()
_chat_clients: set[WebSocket] = set()
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
    for ws in list(_chat_clients):
        try:
            await ws.close()
        except Exception:
            pass
    _chat_clients.clear()
    # 注销 WebSocket 广播 broker，避免测试场景多次 create_app 时 broker 累积
    unregister_broker(_broadcast_message_to_ws)
    logger.info("API 服务已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="Qingci-Bot CE API", version="1.2.0", lifespan=lifespan)

    # CORS：不使用 allow_credentials=True + allow_origins=["*"]（违反 CORS 规范）
    # 安全由 X-API-Key 鉴权保证，CORS 仅放开方法/头；
    # 保持通配允许，否则经局域网 IP / 主机名等非固定源访问 /ui 时，
    # 所有跨源 API 请求会被浏览器拦截（表现为页面无数据，如消息日志空白）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 全局异常处理 ──────────────────────────────────────────
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """全局异常处理器：统一错误响应格式 + 日志记录"""
        logger.error(
            f"未处理的 API 异常: {type(exc).__name__}: {exc} "
            f"path={request.url.path} method={request.method}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "服务器内部错误",
                "error_code": "INTERNAL_ERROR",
                "error_type": type(exc).__name__,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """HTTP 异常统一响应格式"""
        from fastapi import HTTPException as HTTPExc
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": f"HTTP_{exc.status_code}",
            },
        )

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(request: Request, exc: ValidationError):
        """Pydantic 校验异常统一响应"""
        return JSONResponse(
            status_code=422,
            content={
                "detail": "请求参数校验失败",
                "error_code": "VALIDATION_ERROR",
                "errors": exc.errors(),
            },
        )

    # 注册路由
    from api.routes import (
        bot_router, config_router, plugin_router, log_router,
        group_router, auth_router, backup_router,
    )
    from api.audit import router as audit_router
    app.include_router(bot_router, prefix="/api/bot", tags=["Bot"])
    app.include_router(config_router, prefix="/api/config", tags=["Config"])
    app.include_router(plugin_router, prefix="/api/plugin", tags=["Plugin"])
    app.include_router(log_router, prefix="/api/log", tags=["Log"])
    app.include_router(group_router, prefix="/api/group", tags=["Group"])
    app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
    app.include_router(audit_router, prefix="/api/audit", tags=["Audit"])
    app.include_router(backup_router, prefix="/api/backup", tags=["Backup"])

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
        # 二次检查，防止并发超过上限：
        # accept 前检查 + accept 后二次检查已闭合并发窗口，超限连接立即断开
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

    # WebSocket 流式 LLM 对话（对话调试台；鉴权方式同 /api/ws/log）
    # 客户端发送 {"message": "...", "user_id": 900000001}，
    # 服务端逐块返回 {"type":"delta","text":...}，结束返回 {"type":"done"}。
    @app.websocket("/api/ws/chat")
    async def ws_chat(ws: WebSocket, token: str = Query(default="")):
        configured_key = _get_configured_api_key()
        if configured_key is None:
            await ws.close(code=4001, reason="服务暂不可用")
            return
        if configured_key and not secrets.compare_digest(token, configured_key):
            await ws.close(code=4001, reason="未授权")
            return
        # 连接数限制（accept 前检查，独立于日志连接池）
        if len(_chat_clients) >= _MAX_WS_CLIENTS:
            await ws.close(code=4003, reason="连接数已满")
            return
        await ws.accept()
        _chat_clients.add(ws)
        # 二次检查，防止并发超过上限
        if len(_chat_clients) > _MAX_WS_CLIENTS:
            _chat_clients.discard(ws)
            await ws.close(code=4003, reason="连接数已满")
            return
        bot = get_bot()
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "text": "消息格式错误"})
                    continue
                message = str(data.get("message", "")).strip()
                if not message:
                    await ws.send_json({"type": "error", "text": "消息不能为空"})
                    continue
                if bot is None or not bot.is_running or bot.llm is None:
                    await ws.send_json(
                        {"type": "error", "text": "Bot 未运行，请先在顶部启动 Bot"}
                    )
                    continue
                # 调试会话固定为私聊 + 独立 user_id，避免污染真实对话
                try:
                    user_id = int(data.get("user_id") or 0) or 900000001
                except (ValueError, TypeError):
                    await ws.send_json({"type": "error", "text": "user_id 必须为数字"})
                    continue
                stream = bot.llm.chat_stream(
                    message=message, message_type="private", user_id=user_id
                )
                try:
                    async for chunk in stream:
                        await ws.send_json({"type": "delta", "text": chunk})
                finally:
                    # 正常结束或客户端断开均关闭生成器：
                    # chat_stream 内部会在 GeneratorExit 时回滚用户消息
                    await stream.aclose()
                await ws.send_json({"type": "done"})
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("Chat WebSocket 异常断开", exc_info=True)
        finally:
            _chat_clients.discard(ws)

    # 静态文件（Web UI 构建产物）
    # frozen 模式下为 exe 所在目录/web/dist（见 bot/paths.py）
    import os
    import re

    from bot.paths import app_root

    web_dir = str(app_root() / "web" / "dist")
    web_ready = False
    if os.path.isdir(web_dir):
        index_path = os.path.join(web_dir, "index.html")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index_html = f.read()
                # 校验 index.html 引用的资源是否都存在。
                # 要求：存在应用挂载点 + 至少一个本地资源引用 + 引用全部存在，
                # 避免构建产物为空/引用前缀变更时误报 ready。
                refs = [
                    r
                    for r in re.findall(r'(?:src|href)="([^"]+)"', index_html)
                    if not r.startswith(("data:", "#", "http"))
                ]
                missing = []
                for ref in refs:
                    rel = ref[4:] if ref.startswith("/ui/") else ref.lstrip("/")
                    rel = rel.split("?", 1)[0].split("#", 1)[0]
                    if not rel or rel.startswith("../"):
                        continue
                    if not os.path.exists(os.path.join(web_dir, rel)):
                        missing.append(ref)
                if 'id="app"' not in index_html or not refs or missing:
                    logger.warning(
                        f"Web UI 构建产物不完整（missing={missing}），"
                        f"请在 web/ 目录运行 'npm run build' 重新构建"
                    )
                else:
                    web_ready = True
            except Exception:
                logger.exception("检查 Web UI 构建产物失败")

    if web_ready:
        app.mount("/ui", StaticFiles(directory=web_dir, html=True), name="web")

        @app.get("/")
        async def root():
            return RedirectResponse(url="/ui")
    else:
        build_hint = (
            "<h1>Qingci-Bot CE Web UI 未构建</h1>"
            "<p>请在项目根目录执行以下命令构建 Web 界面：</p>"
            "<pre>cd web\nnpm install\nnpm run build</pre>"
        )

        @app.get("/")
        async def root():
            return HTMLResponse(content=build_hint, status_code=200)

    return app
