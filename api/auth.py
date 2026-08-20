"""API 鉴权依赖"""

import ipaddress
import logging
import os
import secrets
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger("qingci-bot.api.auth")

# X-API-Key 请求头
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_config_path: Path | None = None

_cached_key: str | None = None
_cached_mtime: float = 0.0


def set_config_path(path: Path):
    """设置配置文件路径（由 main.py 在启动时调用）"""
    global _config_path, _cached_key, _cached_mtime
    _config_path = path
    # 配置路径变更后旧缓存不再有效，必须清空（否则 _get_configured_api_key
    # 会命中旧文件的 mtime 缓存，返回过期 api_key）
    _cached_key = None
    _cached_mtime = 0.0


def _get_configured_api_key() -> str | None:
    """获取已配置的 API Key

    返回值：
    - None: 配置读取失败（fail-closed，拒绝所有请求）
    - "": 显式未配置 api_key（跳过鉴权）
    - 非空字符串: 已配置 api_key
    """
    global _cached_key, _cached_mtime
    try:
        from bot.core.bot import get_bot

        bot = get_bot()
        if bot and bot.config:
            return bot.config.config.api_key or ""
    except Exception:
        pass
    # 回退：直接读取配置文件（带 mtime 缓存）
    try:
        from bot.config import ConfigManager
        from bot.instances import default_config_path

        path = _config_path or default_config_path()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        if _cached_key is not None and mtime == _cached_mtime:
            return _cached_key
        cm = ConfigManager(path)
        cm.load()
        _cached_key = cm.config.api_key or ""
        _cached_mtime = mtime
        return _cached_key
    except Exception:
        logger.warning("读取配置文件失败，API 鉴权将 fail-closed")
        return None


async def require_auth(request: Request, api_key: str = Depends(_api_key_header)):
    """鉴权依赖：校验 X-API-Key 请求头

    配置中 api_key 为空时的免鉴权豁免仅对环回来源（本机）生效：
    一旦监听地址暴露到局域网/公网（0.0.0.0），非环回请求必须配置 api_key，
    否则拒绝访问，避免整个管理面（含配置/密钥读写）无认证暴露。
    """
    configured_key = _get_configured_api_key()
    if configured_key is None:
        # 配置读取失败，fail-closed
        raise HTTPException(status_code=503, detail="配置读取失败，服务暂不可用")
    if not configured_key:
        if _is_loopback_request(request):
            # 本机访问：显式未配置 api_key，跳过鉴权（本地开发模式）
            return True
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未配置 API Key，禁止非本机访问（请配置 api.api_key 后重启）",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not api_key or not secrets.compare_digest(api_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return True


def _is_loopback_request(request: Request) -> bool:
    """请求是否来自环回地址（127.0.0.1 / ::1 / localhost）"""
    host = request.client.host if request.client else ""
    if not host:
        return False
    # 测试环境：TestClient 固定 client.host="testclient"，视为本机访问
    if os.environ.get("QINGCI_TEST") == "1":
        return True
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
