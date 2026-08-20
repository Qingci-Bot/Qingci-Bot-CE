"""FastAPI 接口层"""

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from api.auth import _get_configured_api_key
from bot.broadcast import register_broker, unregister_broker
from bot.core.bot import QingciBot
from bot.core.bot import get_bot as _get_bot
from bot.logformat import get_runlog_new_nowait, get_runlog_snapshot


def get_bot() -> QingciBot | None:
    try:
        return _get_bot()
    except RuntimeError:
        return None


logger = logging.getLogger("qingci-bot.api")

# WebSocket 连接池（实时消息推送）与对话调试台连接池
_ws_clients: set[WebSocket] = set()
_chat_clients: set[WebSocket] = set()
_runlog_clients: set[asyncio.Queue] = set()  # 运行日志客户端（每条连接一个 asyncio.Queue）
_runlog_pump_task: asyncio.Task | None = None
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
    for ws, result in zip(clients, results, strict=False):
        if isinstance(result, Exception):
            _ws_clients.discard(ws)


async def _broadcast_message_to_ws(message: dict) -> None:
    """通过 WebSocket 广播消息"""
    await _send_to_all_ws(json.dumps(message, ensure_ascii=False))


async def _runlog_pump() -> None:
    """运行日志消费泵：从环形缓冲的新条目队列取日志，扇出给所有 runlog 客户端"""
    while True:
        entry = await asyncio.to_thread(get_runlog_new_nowait)
        if entry is not None:
            payload = json.dumps({"type": "log", "entry": entry}, ensure_ascii=False)
            dead: list[asyncio.Queue] = []
            for q in list(_runlog_clients):
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    # 单客户端积压满则丢弃该连接（从中断开，等其重连）
                    dead.append(q)
            for q in dead:
                _runlog_clients.discard(q)
        else:
            await asyncio.sleep(0.05)


def _ensure_runlog_pump() -> None:
    """确保运行日志消费泵已启动（幂等；由 lifespan 启动 / 客户端连接时兜底）"""
    global _runlog_pump_task
    if _runlog_pump_task is None or _runlog_pump_task.done():
        _runlog_pump_task = asyncio.get_event_loop().create_task(_runlog_pump())


async def _stop_runlog_pump() -> None:
    """停止运行日志消费泵（lifespan 关闭时清理）"""
    global _runlog_pump_task
    if _runlog_pump_task is not None and not _runlog_pump_task.done():
        _runlog_pump_task.cancel()
        try:
            await _runlog_pump_task
        except asyncio.CancelledError:
            pass
    _runlog_pump_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info("API 服务启动")
    _ensure_runlog_pump()
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
    _runlog_clients.clear()
    await _stop_runlog_pump()
    # 注销 WebSocket 广播 broker，避免测试场景多次 create_app 时 broker 累积
    unregister_broker(_broadcast_message_to_ws)
    logger.info("API 服务已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    from bot import __version__

    app = FastAPI(title="Qingci-Bot CE API", version=__version__, lifespan=lifespan)

    # 注入 FastAPI 应用到 PluginManager（供插件注册 Web 管理页面）
    bot = get_bot()
    if bot is not None and hasattr(bot, "plugin_manager"):
        bot.plugin_manager.set_web_app(app)

    # CORS：不使用 allow_credentials=True + allow_origins=["*"]（违反 CORS 规范）
    # 通配 allow_origins=["*"] 会让任意恶意网页跨源读取本地 API 响应
    # （配合未配 api_key 时的环回免鉴权可构成 CSRF 数据窃取）。
    # 收敛为仅环回来源：局域网/主机名访问页面与 API 同源，不受 CORS 影响；
    # 仅拦截"外部域名页面跨源调用本机 API"这一攻击面。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(https?://(127\.0\.0\.1|localhost|\[::1\]|\[0:0:0:0:0:0:0:1\])(:\d+)?)$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 基础安全响应头（X-Frame-Options 兼容 WebUI 在桌面窗口内嵌与插件 iframe）
    @app.middleware("http")
    async def _security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

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
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """HTTP 异常统一响应格式（保留 exc.headers，如 401 的 WWW-Authenticate）"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": f"HTTP_{exc.status_code}",
            },
            headers=exc.headers,
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
    from api.audit import router as audit_router
    from api.routes import (
        backup_router,
        bot_router,
        command_router,
        config_router,
        group_router,
        instances_router,
        log_router,
        login_router,
        market_router,
        plugin_router,
    )

    app.include_router(bot_router, prefix="/api/bot", tags=["Bot"])
    app.include_router(config_router, prefix="/api/config", tags=["Config"])
    app.include_router(plugin_router, prefix="/api/plugin", tags=["Plugin"])
    app.include_router(market_router, prefix="/api/plugins/market", tags=["PluginMarket"])
    app.include_router(log_router, prefix="/api/log", tags=["Log"])
    app.include_router(group_router, prefix="/api/group", tags=["Group"])
    app.include_router(login_router, prefix="/api/auth", tags=["Auth"])
    app.include_router(audit_router, prefix="/api/audit", tags=["Audit"])
    app.include_router(backup_router, prefix="/api/backup", tags=["Backup"])
    app.include_router(command_router, prefix="/api/command", tags=["Command"])
    app.include_router(instances_router, prefix="/api/instances", tags=["Instance"])

    # 注册 WebSocket 广播 broker（register_broker 内部已去重，create_app 多次调用安全）
    register_broker(_broadcast_message_to_ws)

    def _ws_auth(ws: WebSocket, query_token: str) -> tuple[str, str | None]:
        """从 WebSocket 子协议或 query 参数提取鉴权 token。

        浏览器 WebSocket 无法设置自定义 header，若把 API Key 放进 URL query
        会进入访问日志/代理日志；改用子协议（sec-websocket-protocol）传递更安全。
        query 参数保留作为兼容回退。
        """
        sub = ws.headers.get("sec-websocket-protocol", "")
        if sub.startswith("api-key."):
            return sub[len("api-key.") :], sub
        return query_token, None

    def _ws_auth_check(ws: WebSocket, query_token: str) -> tuple[str | None, str | None]:
        """校验 WebSocket 鉴权（与 HTTP 侧 require_auth 对齐）

        - 配置读取失败：fail-closed
        - 未配 key：仅环回来源 + Origin 为环回时才放行（防任意网页订阅实时流）
        - 已配 key：校验子协议/token

        Returns:
            (错误 reason, 子协议)；reason 为 None 表示鉴权通过
        """
        from api.auth import is_loopback_host, is_loopback_origin

        token, subprotocol = _ws_auth(ws, query_token)
        configured_key = _get_configured_api_key()
        if configured_key is None:
            return "服务暂不可用", None
        if not configured_key:
            host = ws.client.host if ws.client else ""
            origin = ws.headers.get("origin", "")
            if not is_loopback_host(host) or (origin and not is_loopback_origin(origin)):
                return "未配置 API Key，禁止非本机访问", None
            return None, subprotocol
        if not secrets.compare_digest(token, configured_key):
            return "未授权", None
        return None, subprotocol

    # WebSocket 实时日志（鉴权：子协议或 token 查询参数传递 API Key）
    @app.websocket("/api/ws/log")
    async def ws_log(ws: WebSocket, token: str = Query(default="")):
        reason, subprotocol = _ws_auth_check(ws, token)
        if reason is not None:
            await ws.close(code=4001, reason=reason)
            return
        # 连接数限制（accept 前检查）
        if len(_ws_clients) >= _MAX_WS_CLIENTS:
            await ws.close(code=4003, reason="连接数已满")
            return
        await ws.accept(subprotocol=subprotocol)
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
        reason, subprotocol = _ws_auth_check(ws, token)
        if reason is not None:
            await ws.close(code=4001, reason=reason)
            return
        # 连接数限制（accept 前检查，独立于日志连接池）
        if len(_chat_clients) >= _MAX_WS_CLIENTS:
            await ws.close(code=4003, reason="连接数已满")
            return
        await ws.accept(subprotocol=subprotocol)
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
                    await ws.send_json({"type": "error", "text": "Bot 未运行，请先在顶部启动 Bot"})
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

    # WebSocket 运行日志：连接即回发环形缓冲快照，随后实时推送运行日志。
    # 鉴权方式同 /api/ws/log；服务端消费扇出，多个连接互相独立。
    @app.websocket("/api/ws/runlog")
    async def ws_runlog(ws: WebSocket, token: str = Query(default="")):
        reason, subprotocol = _ws_auth_check(ws, token)
        if reason is not None:
            await ws.close(code=4001, reason=reason)
            return
        if len(_runlog_clients) >= _MAX_WS_CLIENTS:
            await ws.close(code=4003, reason="连接数已满")
            return
        await ws.accept(subprotocol=subprotocol)
        # 连接即回发当前环形缓冲快照，让新打开的运行日志页立即有历史
        try:
            await ws.send_text(
                json.dumps(
                    {"type": "snapshot", "entries": get_runlog_snapshot()}, ensure_ascii=False
                )
            )
        except Exception:
            await ws.close(code=1011, reason="快照发送失败")
            return
        client_queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        _runlog_clients.add(client_queue)
        _ensure_runlog_pump()
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(client_queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    # 30 秒无日志，发送 ping 保活；客户端断开后连接池会自动回收
                    try:
                        await ws.send_json({"type": "ping"})
                    except Exception:
                        break
                    continue
                await ws.send_text(payload)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("RunLog WebSocket 异常断开", exc_info=True)
        finally:
            _runlog_clients.discard(client_queue)

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
                with open(index_path, encoding="utf-8") as f:
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
