# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""QQ 官方机器人适配器 — QQ 开放平台 Bot API v2

对接 QQ 开放平台（q.qq.com）的官方机器人接口，不涉及任何客户端逆向或
模拟登录：仅使用平台公开的 AccessToken 鉴权 + WebSocket 网关 + OpenAPI。

归一化为 OneBot-12 兼容事件 dict（platform=qq），使插件对平台无感知：

- 鉴权：POST /app/getAppAccessToken 获取 access_token（默认 7200 秒），
  提前 60 秒刷新；调用 OpenAPI 时以 ``Authorization: QQBot <token>`` 携带
- 网关：GET /gateway 取 wss 地址 → Hello(op=10) → Identify(op=2, token 形如
  ``QQBot <token>``) → 周期心跳(op=1, d=最新 s) → Dispatch(op=0)。
  支持 Resume(op=6) 补发断连期间遗漏事件；op=7 重连、op=9 会话失效、
  close code 4004（token 失效）/4006~4007/4900+（会话失效）分别处理
- 消息：GROUP_AT_MESSAGE_CREATE → detail_type=group（QQ 群仅在 @机器人 时
  推送，且正文已由平台去掉 @ 前缀，故事件显式携带 is_at_bot=True）；
  C2C_MESSAGE_CREATE → detail_type=private。附件按 content_type 归一化为
  image / voice / video / file 段，URL 同时写入 file_id 与 url
- 标识：开放平台下发的是不透明 openid 字符串，而框架内部以整数 ID 组织
  会话与数据表。适配器用 blake2b 做「带类型前缀的稳定哈希」映射为 63 位
  正整数（同一 openid 重启后仍得同一 ID，会话与统计不会因重启漂移），
  并维护反向表以便回发消息
- 发送：被动回复携带事件 d.id 作 msg_id，并为每条回复分配递增 msg_seq
  （群聊 5 分钟 / 最多 5 条，单聊 60 分钟 / 最多 4 条；重复 msg_id+msg_seq
  会被平台判重）。富媒体先调 /files 上传取 file_info，再以 msg_type=7 发送，
  单条富媒体消息只承载一个媒体，故多图拆成多条
- 去重：同一条消息可能被重复推送，按 d.id 做有界去重
- 状态：READY 后视为在线；last_heartbeat 随心跳 ACK 更新

范围说明：当前覆盖 QQ 群聊与消息列表单聊两种场景。QQ 频道（guild）使用
另一套事件与 API（AT_MESSAGE_CREATE / 频道消息接口），本适配器未实现；
如确需频道能力，可另建适配器或在 call_api 中以 "/" 开头的原始路径直调。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from qingci_plugin_sdk.segments import Message
from websockets.asyncio.client import connect as ws_connect

from ...errors import AppError
from .base import PlatformAdapter, PlatformNotConfigured, cancel_and_await

logger = logging.getLogger("qingci-bot.platforms.qq")

# 获取 AccessToken（不区分正式/沙箱环境）
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
# OpenAPI 基础地址（正式 / 沙箱）
API_BASE_PROD = "https://api.sgroup.qq.com"
API_BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"

# 默认 intents：仅订阅群聊与单聊消息（GROUP_AND_C2C_EVENT = 1 << 25）
INTENT_GROUP_AND_C2C = 1 << 25

# 网关 opcode
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# 富媒体文件类型：1 图片 / 2 视频 / 3 语音 / 4 文件
FILE_TYPE_IMAGE = 1
FILE_TYPE_VIDEO = 2
FILE_TYPE_VOICE = 3
FILE_TYPE_FILE = 4

# 开放平台「被动回复」窗口与次数上限（秒 / 条）
_PASSIVE_WINDOW_GROUP = 300.0
_PASSIVE_WINDOW_C2C = 3600.0
_PASSIVE_MAX_GROUP = 5
_PASSIVE_MAX_C2C = 4

# access_token 提前刷新余量（秒；平台在到期前 60 秒内换发新 token）
_TOKEN_REFRESH_MARGIN = 60.0

# 断连指数退避：起始/上限间隔（秒）
_BACKOFF_MIN = 2.0
_BACKOFF_MAX = 60.0
# 连续失败达到该值即判定离线并广播断连
_OFFLINE_AFTER = 5

# 会话与去重容量上限（防长期运行内存增长）
_MAX_ID_MAP = 8192
_MAX_MSG_SEQ_ENTRIES = 2048
_MAX_SEEN_IDS = 1024

# 事件类型 → 会话维度
_EVENT_GROUP = "GROUP_AT_MESSAGE_CREATE"
_EVENT_C2C = "C2C_MESSAGE_CREATE"

# 关闭码：需要刷新 token 重连 / 会话不可恢复
_CLOSE_AUTH_FAIL = (4004,)
_CLOSE_SESSION_INVALID = (4006, 4007, 4009, *range(4900, 4914))
_CLOSE_RATE_LIMITED = (4008,)

# OneBot 群管/成员动作：QQ 官方开放平台无对应能力（或需内邀白名单），
# 明确报错而非拼成不存在的路径（会得到 404 之类的晦涩错误）
_UNSUPPORTED_OB_ACTIONS = (
    "set_group_kick",
    "set_group_ban",
    "set_group_whole_ban",
    "set_group_admin",
    "set_group_card",
    "set_group_name",
    "get_group_member_list",
    "get_group_member_info",
    "set_friend_add_request",
    "set_group_add_request",
    "get_friend_list",
    "get_group_list",
)


class QQAPIError(AppError, RuntimeError):
    """QQ 开放平台 API 调用失败（AppError 子类，code=qq_api_error）"""

    code = "qq_api_error"

    def __init__(self, message: str, *, err_code: int = 0, trace_id: str = ""):
        super().__init__(message)
        self.err_code = err_code
        self.trace_id = trace_id


class QQAuthError(QQAPIError):
    """AppID / AppSecret 无效，或 access_token 被拒（HTTP 401 / 鉴权类错误码）"""

    code = "qq_auth_error"


class QQPermissionError(QQAPIError):
    """无接口权限（如群管能力未开通白名单）或机器人不在目标会话中"""

    code = "qq_permission_error"


class QQPassiveWindowError(QQAPIError):
    """被动回复窗口过期 / 次数用尽——需用户再次发消息才能继续回复"""

    code = "qq_passive_window_error"


class QQDuplicateSeqError(QQAPIError):
    """同一 msg_id + msg_seq 重复发送被平台判重"""

    code = "qq_duplicate_seq_error"


def _coerce_err_code(data: dict) -> int:
    """错误码取值：新文档用 err_code，旧接口用 code"""
    for key in ("err_code", "code"):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return 0


def _qq_error(action: str, data: dict) -> QQAPIError:
    """按平台错误码归类，便于上层区分「该重试」「该等用户再发消息」等语义"""
    err_code = _coerce_err_code(data)
    desc = str(data.get("message") or data.get("msg") or "") or "无错误描述"
    trace_id = str(data.get("trace_id") or "")
    text = f"QQ API 错误 ({action}, code={err_code}): {desc}"
    if err_code in (11241, 11242, 11243, 11244, 100016, 100017):
        return QQAuthError(text, err_code=err_code, trace_id=trace_id)
    if err_code in (11253, 40034105, 40034101, 40034102, 40054003, 40034128):
        # 11253 无接口权限 / 40034105 无主动消息权限 / 40034128 被动回复超限
        if err_code == 40034128:
            return QQPassiveWindowError(text, err_code=err_code, trace_id=trace_id)
        return QQPermissionError(text, err_code=err_code, trace_id=trace_id)
    if err_code in (304103, 40034005, 40034024, 40034026):
        return QQPassiveWindowError(text, err_code=err_code, trace_id=trace_id)
    if err_code == 40054005:
        return QQDuplicateSeqError(text, err_code=err_code, trace_id=trace_id)
    return QQAPIError(text, err_code=err_code, trace_id=trace_id)


class QQAdapter(PlatformAdapter):
    """QQ 开放平台机器人适配器（WebSocket 网关 + OpenAPI v2）"""

    name = "qq"
    display_name = "QQ 官方机器人"
    supports_request_approval = False
    supports_media_upload = True
    supports_at_trigger = True

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        *,
        sandbox: bool = False,
        intents: int = INTENT_GROUP_AND_C2C,
        api_base: str = "",
        token_url: str = "",
        request_timeout: float = 30.0,
        max_retries: int = 2,
    ):
        super().__init__()
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.sandbox = bool(sandbox)
        self.intents = int(intents or INTENT_GROUP_AND_C2C)
        self.api_base = str(api_base or "").strip().rstrip("/") or (
            API_BASE_SANDBOX if self.sandbox else API_BASE_PROD
        )
        self.token_url = str(token_url or "").strip() or TOKEN_URL
        self.request_timeout = max(5.0, float(request_timeout or 30.0))
        self.max_retries = max(0, int(max_retries or 0))

        self._running = False
        self._ready = False
        self._ws_task: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None
        self._token = ""
        self._token_expire_at = 0.0
        self._token_lock = asyncio.Lock()

        # 网关会话（Resume 用）
        self._session_id = ""
        self._seq = 0
        self._last_heartbeat = 0.0
        # 最近一次网关连接的存活秒数：重连时据此判断「真的恢复了」还是「又被踢」
        self._last_session_seconds = 0.0
        self.heartbeat_interval = 30.0

        # 机器人自身信息（READY 下发）
        self.self_id = ""
        self.username = ""

        # openid ↔ 稳定整数 ID 双向表（正向用哈希，反向表供回发）
        self._openid_by_id: dict[int, str] = {}
        self._nickname_by_id: dict[int, str] = {}
        # 已记录过「首次出现」的 ID（便于运维把 openid 与内部 ID 对齐后配置管理员）
        self._logged_ids: set[int] = set()

        # 被动回复上下文：target_id → (最近入站 msg_id, 过期时间)
        self._reply_ctx: dict[int, tuple[str, float]] = {}
        # msg_id → 已用 msg_seq（递增，避免平台判重）
        self._msg_seq: dict[str, int] = {}
        # 入站消息去重（同一条消息可能重复推送）
        self._seen: OrderedDict[str, float] = OrderedDict()

        # --- 可观测性 / 连接健康 ---
        self.consecutive_errors = 0
        self.error_count = 0
        self.last_error_time = 0.0
        self.last_disconnect_time = 0.0
        self._disconnected_notified = False

    # ============ HTTP 与鉴权 ============

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout))
        return self._http

    async def _access_token(self, *, force: bool = False) -> str:
        """获取（并缓存）access_token；临近过期或 force 时刷新

        平台在到期前 60 秒内换发新 token，老 token 在该窗口内仍有效，
        因此提前 _TOKEN_REFRESH_MARGIN 刷新不会造成调用中断。
        """
        async with self._token_lock:
            now = time.time()
            if not force and self._token and now < self._token_expire_at - _TOKEN_REFRESH_MARGIN:
                return self._token
            if not self.app_id or not self.app_secret:
                raise QQAuthError("QQ 机器人未配置 app_id / app_secret（platforms.qq）")
            payload = {"appId": self.app_id, "clientSecret": self.app_secret}
            try:
                resp = await self._client().post(self.token_url, json=payload)
            except httpx.TransportError as exc:
                raise QQAPIError(f"获取 QQ access_token 失败（网络异常）: {exc}") from None
            data: dict = {}
            try:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    data = parsed
            except ValueError:
                data = {}
            token = str(data.get("access_token") or "")
            if not token:
                # 该接口鉴权失败时仍返回 HTTP 200 + {code, message}
                raise QQAuthError(
                    "获取 QQ access_token 失败（请核对 AppID / AppSecret）: "
                    f"{data.get('message') or resp.text[:200]}",
                    err_code=_coerce_err_code(data),
                )
            try:
                expires_in = int(data.get("expires_in") or 7200)
            except (TypeError, ValueError):
                expires_in = 7200
            self._token = token
            self._token_expire_at = time.time() + max(60, expires_in)
            logger.debug("QQ access_token 已刷新，有效期 %s 秒", expires_in)
            return token

    async def _api(self, method: str, path: str, *, json_body: dict | None = None) -> dict:
        """调用 OpenAPI（path 以 / 开头），返回响应 JSON

        网络传输错误按 max_retries 有限重试；401 时刷新 token 重试一次；
        业务错误按错误码分类抛出。**不对被动回复类错误重试**——重试会
        白白消耗回复次数额度。
        """
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.api_base}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            token = await self._access_token()
            headers = {
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            try:
                resp = await self._client().request(method, url, headers=headers, json=json_body)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                delay = 0.5 * (attempt + 1)
                logger.warning(
                    "QQ 请求失败，将在 %.1fs 后重试 (%s %s, 第 %d 次): %s",
                    delay,
                    method,
                    path,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 401 and attempt == 0:
                # token 失效：强制刷新后重试一次
                logger.info("QQ access_token 被拒（401），刷新后重试")
                await self._access_token(force=True)
                continue

            data: dict = {}
            if resp.content:
                try:
                    parsed = resp.json()
                    if isinstance(parsed, dict):
                        data = parsed
                except ValueError:
                    data = {}
            if resp.status_code >= 400:
                err = _qq_error(f"{method} {path}", data)
                # 401/403 且未取到业务码时按鉴权/权限归类
                if not err.err_code:
                    if resp.status_code == 401:
                        raise QQAuthError(f"QQ API 鉴权失败 (HTTP 401, {method} {path})") from None
                    if resp.status_code == 403:
                        raise QQPermissionError(
                            f"QQ API 无权限 (HTTP 403, {method} {path})"
                        ) from None
                raise err
            if _coerce_err_code(data) != 0:
                raise _qq_error(f"{method} {path}", data)
            return data
        raise QQAPIError(f"QQ 请求失败 ({method} {path}): {last_exc}") from None

    def set_credentials(self, app_id: str, app_secret: str) -> None:
        """热更新凭据（下次取 token 时生效，并立即作废旧 token）"""
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self._token = ""
        self._token_expire_at = 0.0
        logger.info("QQ 机器人凭据已更新，等待重新鉴权（app_id=%s）", self.app_id)

    # ============ 状态 ============

    def status_info(self) -> dict:
        state = "stopped"
        if self._running:
            if self._ready:
                state = "connected" if self.consecutive_errors == 0 else "degraded"
            else:
                state = "connecting"
        return {
            "connection_state": state,
            "consecutive_errors": self.consecutive_errors,
            "error_count": self.error_count,
            "last_error_time": self.last_error_time,
            "last_disconnect_time": self.last_disconnect_time,
            "backoff": round(self._backoff_delay(), 1),
            "sandbox": self.sandbox,
            "app_id": self.app_id,
            "self_id": self.self_id,
            "username": self.username,
            "session_resumable": bool(self._session_id),
            "token_expire_at": round(self._token_expire_at, 1),
            "known_sessions": len(self._openid_by_id),
        }

    def _backoff_delay(self) -> float:
        if self.consecutive_errors <= 0:
            return _BACKOFF_MIN
        return min(_BACKOFF_MAX, _BACKOFF_MIN * (2.0 ** (self.consecutive_errors - 1)))

    @property
    def is_connected(self) -> bool:
        return bool(self._running and self._ready)

    @property
    def last_heartbeat(self) -> float:
        return self._last_heartbeat

    # ============ 生命周期 ============

    async def start(self) -> None:
        if not self.app_id or not self.app_secret:
            raise PlatformNotConfigured(
                "未配置 AppID / AppSecret —— 请在「系统设置 → QQ 官方机器人连接」中填写"
            )
        self._running = True
        # 先取一次 token：凭据错误在启动阶段就明确报错，而不是等网关异步失败
        try:
            await self._access_token(force=True)
        except Exception as exc:
            self._running = False
            raise RuntimeError(f"QQ 适配器启动失败（鉴权未通过）: {exc}") from exc
        self._ws_task = asyncio.create_task(self._gateway_loop())
        logger.info(
            "QQ 适配器已启动: app_id=%s 环境=%s intents=%s",
            self.app_id,
            "沙箱" if self.sandbox else "正式",
            self.intents,
        )

    async def stop(self) -> None:
        self._running = False
        self._ready = False
        await cancel_and_await(self._ws_task)
        self._ws_task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        logger.info("QQ 适配器已停止")

    # ============ 网关连接 ============

    async def _get_gateway(self) -> str:
        """获取 WebSocket 网关地址"""
        data = await self._api("GET", "/gateway")
        url = str(data.get("url") or "")
        if not url:
            data = await self._api("GET", "/gateway/bot")
            url = str(data.get("url") or "")
        if not url:
            raise QQAPIError("QQ 网关地址获取失败：接口未返回 url")
        return url

    async def _gateway_loop(self) -> None:
        """网关连接主循环：连→（断）→退避重连

        正常关闭（服务端主动断开）立即重连并尝试 Resume；连续异常则指数退避，
        连续失败到阈值广播断连，恢复后广播重连。
        """
        while self._running:
            try:
                url = await self._get_gateway()
                await self._run_gateway(url)
                if not self._running:
                    break
                # 连接被关闭（服务端踢/心跳超时/协议要求重连）。
                #
                # 这里不能无条件 sleep(1) 并清零计数：服务端如果持续断开
                # （鉴权被拒、风控限流），1 秒一次的连-断循环就是重连风暴，
                # 只会让限流更久——这正是「有概率长时间连不上」的成因。
                # 改为按「本次连接活了多久」判断：活过 60 秒才算真正恢复。
                if self._last_session_seconds >= 60:
                    self.consecutive_errors = 0
                    await asyncio.sleep(1.0)
                else:
                    self.consecutive_errors += 1
                    delay = self._backoff_delay()
                    logger.warning(
                        "QQ 网关连接仅存活 %.0fs 即断开，%.1fs 后重连（连续 %d 次）",
                        self._last_session_seconds,
                        delay,
                        self.consecutive_errors,
                    )
                    await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.consecutive_errors += 1
                self.error_count += 1
                self.last_error_time = time.time()
                self._ready = False
                logger.warning("QQ 网关异常（第 %d 次连续）: %s", self.consecutive_errors, exc)
                if self.consecutive_errors >= _OFFLINE_AFTER and not self._disconnected_notified:
                    self._disconnected_notified = True
                    self.last_disconnect_time = self.last_error_time
                    logger.error("QQ 网关连续失败，标记平台离线")
                    await self.notify_disconnected()
                await asyncio.sleep(self._backoff_delay())

    async def _run_gateway(self, url: str) -> None:
        """建立单次 WebSocket 会话（返回即代表连接已结束）"""
        logger.info("QQ 网关连接中: %s", url)
        heartbeat_task: asyncio.Task | None = None
        started = time.time()
        # 用显式 connect 而不是 async with：需要在退出后读 close_code，
        # 才能区分「网络抖动」与「鉴权失败/会话失效」——后者不刷新 token
        # 或不清 session，会一直用过期凭证重连，表现为反复掉线。
        ws = await ws_connect(
            url,
            ping_interval=None,  # 由平台级 op=1 心跳维护，避免双重心跳
            close_timeout=5,
            max_size=2**24,
            open_timeout=self.request_timeout,
        )
        try:
            # 连接建立即重置心跳基准：否则首次 ACK 到达前，超时判断没有参照点
            self._last_heartbeat = started
            async for raw in ws:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    logger.warning("QQ 网关下发非 JSON 数据，已忽略")
                    continue
                if not isinstance(payload, dict):
                    continue
                op = payload.get("op")
                if op == OP_HELLO:
                    interval = self._hello_interval(payload)
                    self.heartbeat_interval = interval
                    await self._send_identity(ws)
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                    continue
                if op == OP_HEARTBEAT_ACK:
                    self._last_heartbeat = time.time()
                    continue
                if op == OP_RECONNECT:
                    logger.info("QQ 网关要求重连（op=7）")
                    return
                if op == OP_INVALID_SESSION:
                    # d=true 可 Resume；false 需清空会话重新 Identify
                    if not payload.get("d"):
                        self._session_id = ""
                        self._seq = 0
                        logger.info("QQ 会话失效，将重新鉴权")
                    return
                if op == OP_DISPATCH:
                    await self._on_dispatch(payload)
        finally:
            self._last_session_seconds = time.time() - started
            code = getattr(ws, "close_code", None)
            if code is not None:
                self._on_close_code(code)
            if heartbeat_task is not None:
                await cancel_and_await(heartbeat_task)
            try:
                await ws.close()
            except Exception:
                logger.debug("关闭 QQ 网关连接时异常", exc_info=True)
            self._ready = False

    def _on_close_code(self, code: int) -> None:
        """按关闭码清理会话状态（不同错误的原因不同，处理也必须不同）

        - 4004：token 无效/过期。只重连不换 token 会一直失败——必须强制刷新
        - 4006/4007、4900+：会话失效，Resume 一定被拒，清空后走 Identify
        其余（1000 正常关闭、1001 服务端重启）保留 session，下次带 Resume
        """
        if code == 4004:
            logger.warning("QQ 网关鉴权失败（4004），将刷新 access_token 后重连")
            self._session_id = ""
            self._seq = 0
            self._token_expire_at = 0.0
            return
        if code in (4006, 4007) or code >= 4900:
            logger.info("QQ 网关会话失效（close=%s），将重新鉴权", code)
            self._session_id = ""
            self._seq = 0

    @staticmethod
    def _hello_interval(payload: dict) -> float:
        data = payload.get("d") or {}
        raw = data.get("heartbeat_interval") if isinstance(data, dict) else None
        if raw is None:
            return 30.0
        try:
            interval_ms = float(raw)
        except (TypeError, ValueError):
            return 30.0
        # 平台下发毫秒；下限 5 秒，避免异常值导致心跳风暴
        return max(5.0, interval_ms / 1000.0)

    async def _send_identity(self, ws: Any) -> None:
        """有可恢复会话则 Resume，否则 Identify"""
        token = await self._access_token()
        if self._session_id:
            payload = {
                "op": OP_RESUME,
                "d": {
                    "token": f"QQBot {token}",
                    "session_id": self._session_id,
                    "seq": self._seq,
                },
            }
            logger.info("QQ 网关恢复会话（session_id=%s seq=%s）", self._session_id, self._seq)
        else:
            payload = {
                "op": OP_IDENTIFY,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": self.intents,
                    "shard": [0, 1],
                    "properties": {
                        "$os": "linux",
                        "$browser": "qingci-bot-ce",
                        "$device": "qingci-bot-ce",
                    },
                },
            }
        await ws.send(json.dumps(payload))

    async def _heartbeat_loop(self, ws: Any) -> None:
        """周期性发送心跳，并在长时间收不到 ACK 时主动断开

        为什么必须检测 ACK：NAT / 中间设备可能在链路上静默丢弃会话，此时
        TCP 处于半开状态——``send`` 仍会成功（数据进了内核缓冲），但服务端
        早已不认这条连接，接收循环也不会结束。表现就是「适配器显示在线，
        但消息不再到达」，直到用户重启才恢复。用「心跳发出后长时间没有 ACK」
        作为判据主动关闭，接收循环才会退出并触发重连。
        """
        # 允许 2.5 个心跳周期没有 ACK；下限 60 秒，避免平台下发极短间隔时误判
        stale_after = max(60.0, self.heartbeat_interval * 2.5)
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            waited = time.time() - self._last_heartbeat
            if self._last_heartbeat and waited > stale_after:
                logger.warning("QQ 心跳超时（%.0fs 未收到 ACK），主动断开以触发重连", waited)
                try:
                    await ws.close(code=1000, reason="heartbeat timeout")
                except Exception:
                    logger.debug("心跳超时后关闭连接异常", exc_info=True)
                return
            try:
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq or None}))
            except Exception:
                # 连接已断：由接收循环负责收尾，这里退出即可
                logger.debug("QQ 心跳发送失败，连接可能已断开", exc_info=True)
                return

    async def _on_dispatch(self, payload: dict) -> None:
        """处理 op=0 事件推送"""
        seq = payload.get("s")
        try:
            if seq is not None:
                self._seq = max(self._seq, int(seq))
        except (TypeError, ValueError):
            pass
        event_type = str(payload.get("t") or "")
        data = payload.get("d")

        if event_type == "READY":
            await self._on_ready(data if isinstance(data, dict) else {})
            return
        if event_type == "RESUMED":
            logger.info("QQ 网关会话已恢复，补发完成")
            await self.notify_reconnected()
            return
        if not isinstance(data, dict):
            return
        if event_type in (_EVENT_GROUP, _EVENT_C2C):
            await self._handle_message_event(event_type, data)
            return
        # 其余事件（好友增删、群机器人增删等）以 meta 事件上报，供插件按需消费
        await self.emit_metaevent(
            {"type": "meta", "detail_type": event_type.lower(), "platform": self.name, "raw": data}
        )

    async def _on_ready(self, data: dict) -> None:
        self._session_id = str(data.get("session_id") or "")
        user = data.get("user") or {}
        if isinstance(user, dict):
            self.self_id = str(user.get("id") or "")
            self.username = str(user.get("username") or "")
        self._ready = True
        self.consecutive_errors = 0
        self._last_heartbeat = time.time()
        if self._disconnected_notified:
            self._disconnected_notified = False
            logger.info("QQ 网关已恢复，广播重连")
            await self.notify_reconnected()
        else:
            logger.info(
                "QQ 机器人就绪: %s (self_id=%s, 心跳 %.0fs)",
                self.username or "未命名",
                self.self_id,
                self.heartbeat_interval,
            )
            await self.notify_connected()

    # ============ 入站消息归一化 ============

    def _stable_id(self, kind: str, openid: str) -> int:
        """把不透明 openid 稳定映射为 63 位正整数

        带类型前缀（g/u）哈希，避免群 openid 与用户 openid 映射到同一 ID；
        纯函数计算，重启后同一 openid 仍得同一 ID，会话与统计不会漂移。
        """
        if not openid:
            return 0
        digest = hashlib.blake2b(f"{kind}:{openid}".encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big") & 0x7FFF_FFFF_FFFF_FFFF
        return value or 1

    def _remember(self, kind: str, openid: str, nickname: str = "") -> int:
        """登记 openid → 整数 ID（含反向表，供回发消息）"""
        stable = self._stable_id(kind, openid)
        if not stable:
            return 0
        self._openid_by_id[stable] = openid
        if nickname:
            self._nickname_by_id[stable] = nickname
        if len(self._openid_by_id) > _MAX_ID_MAP:
            # 有界淘汰：丢掉最早登记的（仅影响重启后的主动发送，不影响被动回复）
            for stale in list(self._openid_by_id)[: _MAX_ID_MAP // 4]:
                self._openid_by_id.pop(stale, None)
                self._nickname_by_id.pop(stale, None)
        return stable

    def _openid_of(self, target_id: int) -> str:
        return str(self._openid_by_id.get(int(target_id)) or "")

    def _log_first_seen(self, stable_id: int, label: str, openid: str, nickname: str) -> None:
        """首次出现的会话记一条 INFO，便于运维把 openid 与内部整数 ID 对齐

        开放平台只给不透明 openid，而权限配置（super_admin / admin_users /
        黑名单）用的是内部整数 ID，两者需要一个可查的对应关系。
        """
        if not stable_id or stable_id in self._logged_ids:
            return
        if len(self._logged_ids) > _MAX_ID_MAP:
            self._logged_ids.clear()
        self._logged_ids.add(stable_id)
        suffix = f" 昵称={nickname}" if nickname else ""
        logger.info("QQ %s首次出现：id=%s（openid=%s）%s", label, stable_id, openid, suffix)

    @staticmethod
    def _parse_timestamp(raw: Any) -> float:
        """RFC3339 → epoch 秒（失败回退当前时间）"""
        if raw in (None, ""):
            return time.time()
        text = str(raw).strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        try:
            return float(text)
        except (TypeError, ValueError):
            return time.time()

    @staticmethod
    def _attachment_segments(attachments: Any) -> tuple[list[dict], list[str]]:
        """把平台附件列表归一化为 v12 段数组（返回 段列表, 图片引用列表）

        附件已带可公开访问的 URL，同时写入 file_id 与 url：下游
        `media.resolve_image` 对 http(s) 直接透传，无需再调平台接口。
        """
        segments: list[dict] = []
        images: list[str] = []
        if not isinstance(attachments, list):
            return segments, images
        for item in attachments:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url:
                continue
            ctype = str(item.get("content_type") or "").lower()
            filename = str(item.get("filename") or "")
            if ctype.startswith("image/"):
                segments.append({"type": "image", "data": {"file_id": url, "url": url}})
                images.append(url)
            elif ctype.startswith("video/"):
                segments.append({"type": "video", "data": {"file_id": url, "url": url}})
            elif ctype == "voice":
                # 语音附件的内容类型就是字面量 voice；平台另给转换后的 WAV 地址
                data = {"file_id": url, "url": url}
                wav = str(item.get("voice_wav_url") or "")
                if wav:
                    data["wav_url"] = wav
                segments.append({"type": "voice", "data": data})
            else:
                data = {"file_id": url, "url": url}
                if filename:
                    data["name"] = filename
                segments.append({"type": "file", "data": data})
        return segments, images

    def _normalize_message(self, event_type: str, data: dict) -> dict | None:
        """把 QQ 群/单聊消息事件归一化为 OneBot 12 消息事件 dict"""
        raw_author = data.get("author")
        author: dict = raw_author if isinstance(raw_author, dict) else {}
        if event_type == _EVENT_GROUP:
            detail_type = "group"
            sub_type = "normal"
            group_openid = str(data.get("group_openid") or "")
            user_openid = str(author.get("member_openid") or author.get("id") or "")
        else:
            detail_type = "private"
            sub_type = "friend"
            group_openid = ""
            user_openid = str(author.get("user_openid") or author.get("id") or "")
        if not user_openid and not group_openid:
            return None

        nickname = str(author.get("username") or author.get("member_nickname") or "")
        user_id = self._remember("u", user_openid, nickname)
        group_id = self._remember("g", group_openid) if group_openid else 0
        self._log_first_seen(user_id, "用户", user_openid, nickname)
        if group_id:
            self._log_first_seen(group_id, "群", group_openid, "")

        # 群消息正文已被平台去掉 @机器人 前缀，仅需去掉平台附加的前导空白
        text = str(data.get("content") or "").lstrip()
        segments: list[dict] = []
        if text:
            segments.append({"type": "text", "data": {"text": text}})
        more_segments, images = self._attachment_segments(data.get("attachments"))
        segments.extend(more_segments)

        message_id = str(data.get("id") or "")
        self._remember_reply_target(
            group_id if detail_type == "group" else user_id, message_id, detail_type
        )
        if not nickname:
            nickname = user_openid

        return {
            "type": "message",
            "detail_type": detail_type,
            "sub_type": sub_type,
            "id": message_id,
            "impl": self.name,
            "platform": self.name,
            "self_id": self.self_id,
            "time": self._parse_timestamp(data.get("timestamp")),
            "message_id": message_id,
            "message": segments,
            "alt_message": text,
            "user_id": str(user_id),
            "group_id": str(group_id) if detail_type == "group" else "",
            "sender": {
                "user_id": str(user_id),
                "nickname": nickname,
                "card": nickname,
                "platform": self.name,
                # 原始 openid：插件可据此与开放平台侧数据对齐；核心内部仍用整数 ID
                "openid": user_openid,
            },
            "raw_message": text,
            "plain_text": text,
            # 群消息仅在 @机器人 时推送，且没有 mention 段可推导，故显式声明
            "at_list": [],
            "is_at_bot": detail_type == "group",
            "images": images,
            "_qq_event": event_type,
            "_qq_raw": data,
        }

    def _remember_reply_target(self, target_id: int, message_id: str, detail_type: str) -> None:
        """记录被动回复上下文（target → 最近入站 msg_id + 过期时间）"""
        if not target_id or not message_id:
            return
        window = _PASSIVE_WINDOW_GROUP if detail_type == "group" else _PASSIVE_WINDOW_C2C
        now = time.time()
        # 顺带清理过期项，避免长期运行累积
        for key, (_, expire_at) in list(self._reply_ctx.items()):
            if expire_at <= now:
                self._reply_ctx.pop(key, None)
        self._reply_ctx[target_id] = (message_id, now + window)

    def _mark_seen(self, message_id: str) -> bool:
        """入站去重：首次出现返回 True；重复推送返回 False"""
        if not message_id:
            return True
        if message_id in self._seen:
            return False
        self._seen[message_id] = time.time()
        while len(self._seen) > _MAX_SEEN_IDS:
            self._seen.popitem(last=False)
        return True

    async def _handle_message_event(self, event_type: str, data: dict) -> None:
        message_id = str(data.get("id") or "")
        if not self._mark_seen(message_id):
            logger.debug("QQ 重复推送的同一条消息已忽略: %s", message_id)
            return
        event = self._normalize_message(event_type, data)
        if event is None:
            logger.warning("QQ 消息事件缺少用户/群标识，已跳过: %s", event_type)
            return
        await self.emit_event(event)

    # ============ 发送 ============

    def _next_seq(self, msg_id: str) -> int:
        """为 msg_id 分配下一个被动回复序号（同一 msg_id 必须递增）"""
        seq = self._msg_seq.get(msg_id, 0) + 1
        self._msg_seq[msg_id] = seq
        if len(self._msg_seq) > _MAX_MSG_SEQ_ENTRIES:
            for stale in list(self._msg_seq)[: _MAX_MSG_SEQ_ENTRIES // 4]:
                self._msg_seq.pop(stale, None)
        return seq

    def _resolve_passive(self, target_id: int, reply_id: str) -> tuple[str, int]:
        """解析本次发送的 (msg_id, msg_seq)

        msg_id 优先取 reply 段携带的消息 ID（明确回复某条），否则用该会话
        最近一条入站消息；两者都没有则退化为主动消息（平台可能拒绝）。
        """
        expired = False
        msg_id = reply_id
        if not msg_id:
            ctx = self._reply_ctx.get(target_id)
            if ctx and ctx[1] > time.time():
                msg_id = ctx[0]
            elif ctx:
                expired = True
                self._reply_ctx.pop(target_id, None)
        if not msg_id:
            if expired:
                logger.warning(
                    "QQ 被动回复窗口已过期（target=%s），将尝试主动消息（可能被平台拒绝）",
                    target_id,
                )
            return "", 0
        return msg_id, self._next_seq(msg_id)

    def _parse_outgoing(self, message: str | list) -> tuple[list[tuple[str, str]], str, str]:
        """解析出站消息段 → (媒体列表, 文本, 回复消息 ID)

        媒体列表元素为 (段类型, 文件引用)；文本中 mention 渲染为 @昵称
        （昵称取自入站事件登记表，未知则省略，避免露出内部 ID）。
        """
        media: list[tuple[str, str]] = []
        text_parts: list[str] = []
        reply_id = ""
        for seg in Message.from_raw(message).as_dicts():
            seg_type = str(seg.get("type") or "")
            raw_data = seg.get("data")
            data: dict = raw_data if isinstance(raw_data, dict) else {}
            if seg_type in ("image", "voice", "record", "video", "file"):
                ref = str(data.get("file_id") or data.get("url") or data.get("file") or "")
                if ref:
                    media.append(("voice" if seg_type == "record" else seg_type, ref))
            elif seg_type == "reply":
                reply_id = str(data.get("message_id") or data.get("id") or "").strip()
            elif seg_type == "text":
                text_parts.append(str(data.get("text") or ""))
            elif seg_type == "mention":
                nickname = self._nickname_by_id.get(self._stable_id_safe(data.get("user_id")), "")
                if nickname:
                    text_parts.append(f"@{nickname} ")
            elif seg_type == "mention_all":
                text_parts.append("@全体成员 ")
        text = re.sub(r"[ \t]+", " ", "".join(text_parts)).strip()
        return media, text, reply_id

    def _stable_id_safe(self, raw: Any) -> int:
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    async def send_msg(self, message_type: str, target_id: int, message: str | list) -> dict:
        """发送消息（文本 / 富媒体）

        target_id 为入站事件归一化后的整数 ID，经反向表还原 openid。
        """
        try:
            target = int(target_id)
        except (TypeError, ValueError):
            raise ValueError(f"QQ 发送失败：无效的 target_id={target_id!r}") from None
        openid = self._openid_of(target)
        if not openid:
            raise ValueError(
                "QQ 发送失败：目标会话不在已知列表内。QQ 官方机器人没有好友/群列表接口，"
                f"只能回复曾经来过消息的会话（target_id={target}）；请让用户先发一条消息。"
            )
        detail_type = "group" if message_type == "group" else "private"
        media, text, reply_id = self._parse_outgoing(message)

        if not text and not media:
            raise ValueError("QQ 发送失败：消息内容为空")

        msg_id, msg_seq = self._resolve_passive(target, reply_id)
        limit = _PASSIVE_MAX_GROUP if detail_type == "group" else _PASSIVE_MAX_C2C
        need = max(1, len(media))
        if msg_id and msg_seq + need - 1 > limit:
            raise QQPassiveWindowError(
                f"QQ 被动回复次数不足（已用 {msg_seq - 1}/{limit}，本次需 {need} 条，"
                f"场景 {detail_type}）。平台限制每条消息的回复条数，"
                "需用户再次发消息后继续。",
                err_code=40034128,
            )

        if not media:
            return await self._send_text(openid, detail_type, text, msg_id, msg_seq)

        # 富媒体：单条消息只能承载一个媒体，故逐条发送；文本随首个媒体作说明
        result: dict = {}
        for index, (kind, ref) in enumerate(media):
            file_info = await self._upload(openid, detail_type, kind, ref)
            payload: dict = {"msg_type": 7, "media": {"file_info": file_info}}
            if text and index == 0:
                payload["content"] = text
            seq = msg_seq + index
            if msg_id:
                payload["msg_id"] = msg_id
                payload["msg_seq"] = seq
            result = await self._api(
                "POST", self._message_path(openid, detail_type), json_body=payload
            )
        if msg_id:
            # 逐条发送已消耗多个序号，把计数器推进到最后一条，避免下次重号
            self._msg_seq[msg_id] = msg_seq + len(media) - 1
        return result

    @staticmethod
    def _message_path(openid: str, detail_type: str) -> str:
        if detail_type == "group":
            return f"/v2/groups/{openid}/messages"
        return f"/v2/users/{openid}/messages"

    @staticmethod
    def _file_path(openid: str, detail_type: str) -> str:
        if detail_type == "group":
            return f"/v2/groups/{openid}/files"
        return f"/v2/users/{openid}/files"

    async def _send_text(
        self, openid: str, detail_type: str, text: str, msg_id: str, msg_seq: int
    ) -> dict:
        payload: dict = {"msg_type": 0, "content": text}
        if msg_id:
            payload["msg_id"] = msg_id
            payload["msg_seq"] = msg_seq
        return await self._api("POST", self._message_path(openid, detail_type), json_body=payload)

    async def _upload(self, openid: str, detail_type: str, kind: str, ref: str) -> str:
        """上传富媒体并返回 file_info

        公网 URL 走 url 字段；本地路径 / base64 / data URI 走 file_data（base64）。
        """
        file_type = {
            "image": FILE_TYPE_IMAGE,
            "video": FILE_TYPE_VIDEO,
            "voice": FILE_TYPE_VOICE,
            "file": FILE_TYPE_FILE,
        }.get(kind, FILE_TYPE_FILE)
        body: dict = {"file_type": file_type, "srv_send_msg": False}
        name = ""
        if ref.startswith(("http://", "https://")):
            body["url"] = ref
        else:
            content, name = self._read_local(ref)
            body["file_data"] = base64.b64encode(content).decode("ascii")
        if file_type == FILE_TYPE_FILE and name:
            body["file_name"] = name
        data = await self._api("POST", self._file_path(openid, detail_type), json_body=body)
        file_info = str(data.get("file_info") or "")
        if not file_info:
            raise QQAPIError("QQ 富媒体上传失败：接口未返回 file_info")
        return file_info

    @staticmethod
    def _read_local(ref: str) -> tuple[bytes, str]:
        """读取本地/内联媒体，返回 (字节, 文件名)"""
        if ref.startswith("base64://"):
            return base64.b64decode(ref[len("base64://") :]), ""
        if ref.startswith("data:"):
            _, _, payload = ref.partition(",")
            return base64.b64decode(payload), ""
        path = Path(ref)
        if not path.is_file():
            raise ValueError(f"QQ 富媒体发送失败：文件不存在或不是公网 URL: {ref}")
        return path.read_bytes(), path.name

    # ============ 能力透传 ============

    async def call_api(self, action: str, params: dict | None = None, timeout: float = 30) -> dict:
        """能力透传

        - send_private_msg / send_group_msg：走统一发送链路
        - get_image / get_file：QQ 附件本身就是可访问 URL，直接回传
        - 群管类 OneBot 动作：官方开放平台无此能力，明确报错
        - 以 ``/`` 开头的 action：按原始路径直调 OpenAPI（高级用法/逃生口）
        """
        params = dict(params or {})
        for hook in list(self._api_call_hooks):
            try:
                modified = hook(action, dict(params))
                if asyncio.iscoroutine(modified) or hasattr(modified, "__await__"):
                    modified = await modified
                if modified is not None:
                    params = modified
            except Exception:
                logger.exception("平台接口调用钩子异常: %s", action)
                raise

        if action in ("send_private_msg", "send_group_msg"):
            detail_type = "group" if action == "send_group_msg" else "private"
            key = "group_id" if detail_type == "group" else "user_id"
            return await self.send_msg(detail_type, params.get(key, 0), params.get("message", ""))
        if action in ("get_image", "get_file"):
            ref = str(params.get("file_id") or params.get("file") or params.get("url") or "")
            if not ref:
                return {}
            return {"file_id": ref, "url": ref}
        if action == "get_group_info":
            gid = self._stable_id_safe(params.get("group_id"))
            return {
                "group_id": gid,
                "group_name": f"QQ群({gid})",
                "group_openid": self._openid_of(gid),
            }
        if action in _UNSUPPORTED_OB_ACTIONS or action.startswith(
            ("set_group_", "get_group_member_", "set_friend_", "get_friend_")
        ):
            raise NotImplementedError(
                f"QQ 官方机器人不支持 OneBot 动作: {action}"
                "（开放平台无对应能力，或需内邀白名单权限）"
            )
        if action.startswith("/"):
            method = str(params.pop("_method", "POST")).upper()
            return await self._api(method, action, json_body=params or None)
        raise NotImplementedError(
            f"QQ 适配器不识别动作: {action}；如需直调开放平台接口，"
            '请用 call_api("/v2/...", {"_method": "POST", ...})'
        )
