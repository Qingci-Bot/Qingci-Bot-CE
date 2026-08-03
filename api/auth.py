"""API 鉴权依赖"""

import secrets
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

# X-API-Key 请求头
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_config_path: Optional[Path] = None


def set_config_path(path: Path):
    """设置配置文件路径（由 main.py 在启动时调用）"""
    global _config_path
    _config_path = path


def _get_configured_api_key() -> str:
    """获取已配置的 API Key"""
    try:
        from bot.core.bot import get_bot
        bot = get_bot()
        if bot and bot.config:
            return bot.config.config.api_key or ""
    except Exception:
        pass
    # 回退：直接读取配置文件
    try:
        from bot.config import ConfigManager, DEFAULT_CONFIG_PATH
        path = _config_path or DEFAULT_CONFIG_PATH
        cm = ConfigManager(path)
        cm.load()
        return cm.config.api_key or ""
    except Exception:
        return ""


async def require_auth(api_key: str = Depends(_api_key_header)):
    """鉴权依赖：校验 X-API-Key 请求头

    如果配置中 api_key 为空，则跳过鉴权（本地开发模式）。
    """
    configured_key = _get_configured_api_key()
    if not configured_key:
        # 未配置 api_key，跳过鉴权
        return True
    if not api_key or not secrets.compare_digest(api_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return True
