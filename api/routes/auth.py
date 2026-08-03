"""登录与鉴权状态接口（不引入 JWT）

- GET  /api/auth/status: 免鉴权，返回是否需要登录（配置 api_key 是否非空）
- POST /api/auth/login:  免鉴权，校验 body 中的 api_key 与配置 key 是否一致

前端登录成功后将 api_key 存入本地，后续请求经 X-API-Key 头走 require_auth。
登录接口带内存防暴力限流：同一来源 IP 连续失败达阈值后冷却期内返回 429。
"""

import logging
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from api.auth import _get_configured_api_key
from api.audit import record_audit

logger = logging.getLogger("qingci-bot.api.routes.auth")

router = APIRouter()

# ============ 登录防暴力限流（进程内字典，重启清零） ============

_LOGIN_FAIL_LIMIT = 5          # 连续失败次数阈值
_LOGIN_COOLDOWN_SECONDS = 60   # 达阈后的冷却时间（秒）
_MAX_TRACKED_IPS = 256         # 记录表容量保护上限

# 来源 IP -> [连续失败次数, 最近失败时间戳]
_login_failures: dict[str, list] = {}


def _client_ip(request: Request) -> str:
    """提取来源 IP（无客户端信息时用占位符）"""
    return request.client.host if request.client else "unknown"


def _purge_expired_login_failures() -> None:
    """清理已解除冷却的记录，防止字典无限增长"""
    now = time.time()
    expired = [
        ip for ip, (_, last) in _login_failures.items()
        if now - last >= _LOGIN_COOLDOWN_SECONDS
    ]
    for ip in expired:
        _login_failures.pop(ip, None)


def _check_login_rate_limit(ip: str) -> None:
    """检查是否处于冷却期，是则抛 429"""
    record = _login_failures.get(ip)
    if record is not None and record[0] >= _LOGIN_FAIL_LIMIT:
        if time.time() - record[1] < _LOGIN_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录失败次数过多，请稍后再试",
            )
        # 冷却已过，重置计数
        _login_failures.pop(ip, None)


def _record_login_failure(ip: str) -> None:
    """记录一次失败；表过大时先清理过期条目"""
    if len(_login_failures) >= _MAX_TRACKED_IPS:
        _purge_expired_login_failures()
    record = _login_failures.get(ip)
    if record is None:
        _login_failures[ip] = [1, time.time()]
    else:
        record[0] += 1
        record[1] = time.time()
    logger.warning(f"登录失败计数: ip={ip}, count={_login_failures[ip][0]}")


class LoginRequest(BaseModel):
    """登录请求体"""
    api_key: str


@router.get("/status")
async def auth_status():
    """鉴权状态：auth_required 为 True 时前端应展示登录页"""
    configured_key = _get_configured_api_key()
    if configured_key is None:
        # 配置读取失败，fail-closed（与 require_auth 行为一致）
        raise HTTPException(status_code=503, detail="配置读取失败，服务暂不可用")
    return {"auth_required": bool(configured_key)}


@router.post("/login")
async def login(data: LoginRequest, request: Request):
    """登录：与配置 api_key 做常量时间比较

    - api_key 未配置（本地开发模式）：直接返回 ok
    - 校验失败：401；同一 IP 连续失败达阈值后冷却期内返回 429
    """
    configured_key = _get_configured_api_key()
    if configured_key is None:
        raise HTTPException(status_code=503, detail="配置读取失败，服务暂不可用")
    ip = _client_ip(request)
    # 防暴力：冷却期内直接拒绝，不消耗比较逻辑
    _check_login_rate_limit(ip)
    if not configured_key:
        # 未配置 api_key，免鉴权模式
        _login_failures.pop(ip, None)
        await record_audit("login", "登录成功（未配置 api_key，免鉴权）", request)
        return {"ok": True}
    if data.api_key and secrets.compare_digest(data.api_key, configured_key):
        # 成功登录清零该 IP 的失败计数
        _login_failures.pop(ip, None)
        await record_audit("login", "登录成功", request)
        return {"ok": True}
    _record_login_failure(ip)
    await record_audit("login", "登录失败：API Key 无效", request)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的 API Key",
    )
