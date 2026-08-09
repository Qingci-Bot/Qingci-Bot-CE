"""配置管理接口"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import ValidationError

from bot.core.bot import get_bot as _get_bot
from bot.core.tasks import spawn_background_task
from bot.config import ConfigManager, LLMConfig, LLM_PROVIDER_PRESETS
from bot.llm.manager import LLMManager
from api.auth import require_auth
from api.audit import record_audit

logger = logging.getLogger("qingci-bot.api.config")

# 配置锁：惰性创建，避免模块导入时绑定到（可能不同的）事件循环
_config_lock: Optional[asyncio.Lock] = None


def _get_config_lock() -> asyncio.Lock:
    """获取配置锁（在当前事件循环中惰性创建）"""
    global _config_lock
    if _config_lock is None:
        _config_lock = asyncio.Lock()
    return _config_lock


# Bot 未运行时的 ConfigManager 缓存，避免每次请求重复创建/读取文件
_fallback_cfg: Optional[ConfigManager] = None
_fallback_path: Optional[str] = None


def _get_config_manager() -> ConfigManager:
    """获取 Bot 实例的 ConfigManager，若 Bot 未初始化则使用自定义路径（带缓存）"""
    try:
        bot = _get_bot()
        return bot.config
    except RuntimeError:
        from api.auth import _config_path
        from bot.config import DEFAULT_CONFIG_PATH
        global _fallback_cfg, _fallback_path
        path = _config_path or DEFAULT_CONFIG_PATH
        if _fallback_cfg is None or path != _fallback_path:
            _fallback_cfg = ConfigManager(path)
            _fallback_cfg.load()
            _fallback_path = path
        return _fallback_cfg


def _maybe_notify_bot(reload_llm: bool = True):
    """通知 Bot 配置变更（异步执行，不阻塞 HTTP 响应）

    引用约定：config.reload() 会新建 AppConfig 实例，经 ConfigManager
    属性访问的组件自动取新值；但缓存了子配置对象引用的组件（如
    LLMManager）必须在此显式传递新引用。新增此类消费方时务必在
    此补充传参。
    """
    try:
        bot = _get_bot()
        if bot:
            bot.config.reload()
            if reload_llm and bot.llm:
                # 异步执行 reload，不阻塞 HTTP 请求；保存任务引用并记录异常。
                # config.reload() 会新建 AppConfig，须把新的 session_summary
                # 对象与用量开关一并传入，否则 LLMManager 持有的旧引用不会生效
                spawn_background_task(
                    bot.llm.reload(
                        bot.config.llm,
                        bot.config.session_summary,
                        bot.config.log.usage_tracking,
                    ),
                    name="llm-reload",
                )
    except RuntimeError:
        pass  # Bot 未运行
    except Exception:
        logger.exception("通知 Bot 配置变更失败")


_SENSITIVE_KEYS = {"api_key", "access_token"}


def _mask_sensitive(data: dict) -> dict:
    """递归脱敏敏感字段（api_key / access_token），返回副本"""
    masked = {}
    for k, v in data.items():
        if isinstance(v, dict):
            masked[k] = _mask_sensitive(v)
        elif k in _SENSITIVE_KEYS and v:
            masked[k] = "***"
        else:
            masked[k] = v
    return masked


def _filter_masked(data: dict) -> dict:
    """过滤掉脱敏占位符 "***"，保留原值"""
    filtered = {}
    for k, v in data.items():
        if v == "***":
            continue  # 跳过脱敏占位符
        if isinstance(v, dict):
            filtered[k] = _filter_masked(v)
        elif isinstance(v, list):
            filtered[k] = [_filter_masked(i) if isinstance(i, dict) else i
                           for i in v if i != "***"]
        else:
            filtered[k] = v
    return filtered


def _deep_merge(base: dict, update: dict) -> dict:
    """深度合并 update 到 base"""
    for k, v in update.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


router = APIRouter()


@router.get("", dependencies=[Depends(require_auth)])
async def get_config():
    """获取完整配置（敏感字段脱敏）"""
    return _mask_sensitive(_get_config_manager().to_dict())


@router.put("", dependencies=[Depends(require_auth)])
async def update_config(data: dict, request: Request):
    """更新配置"""
    async with _get_config_lock():
        try:
            cfg = _get_config_manager()
            current = cfg.to_dict()
            # 过滤脱敏占位符后深度合并
            filtered = _filter_masked(data)
            _deep_merge(current, filtered)
            cfg.update(current)
            _maybe_notify_bot(reload_llm=True)
            # 审计埋点：仅记录变更的顶层字段名，不含字段值（避免泄露密钥）
            await record_audit(
                "config_update", f"更新全局配置，字段: {sorted(filtered.keys())}", request
            )
            return {"message": "配置已更新", "config": _mask_sensitive(cfg.to_dict())}
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("配置更新失败")
            raise HTTPException(status_code=500, detail="内部错误，详见服务端日志")


@router.get("/bot", dependencies=[Depends(require_auth)])
async def get_bot_config():
    """获取 Bot 配置（无敏感字段）"""
    return _get_config_manager().bot.model_dump()


@router.put("/bot", dependencies=[Depends(require_auth)])
async def update_bot_config(data: dict, request: Request):
    """更新 Bot 配置（深度合并，未传入字段保留原值）"""
    async with _get_config_lock():
        try:
            cfg = _get_config_manager()
            current = cfg.bot.model_dump()
            _deep_merge(current, {k: v for k, v in data.items() if v is not None})
            full = cfg.to_dict()
            full["bot"] = current
            cfg.update(full)
            _maybe_notify_bot(reload_llm=False)
            # 审计埋点：仅记录变更字段名
            await record_audit(
                "config_update_bot", f"更新 Bot 配置，字段: {sorted(data.keys())}", request
            )
            return {"message": "Bot 配置已更新"}
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("配置更新失败")
            raise HTTPException(status_code=500, detail="内部错误，详见服务端日志")


@router.get("/llm", dependencies=[Depends(require_auth)])
async def get_llm_config():
    """获取 LLM 配置（api_key 脱敏）"""
    data = _get_config_manager().llm.model_dump()
    return _mask_sensitive(data)


@router.get("/llm/presets", dependencies=[Depends(require_auth)])
async def get_llm_presets():
    """获取 LLM 提供商预设列表（api_url + 推荐 model）"""
    return {
        "providers": list(LLM_PROVIDER_PRESETS.keys()),
        "presets": LLM_PROVIDER_PRESETS,
    }


@router.put("/llm", dependencies=[Depends(require_auth)])
async def update_llm_config(data: dict, request: Request):
    """更新 LLM 配置（深度合并）"""
    async with _get_config_lock():
        try:
            cfg = _get_config_manager()
            current = cfg.llm.model_dump()
            # 过滤 None 值与脱敏占位 "***"，避免覆盖已有配置
            for k, v in data.items():
                if v is not None and v != "***":
                    current[k] = v
            if current.get("provider") == "custom" and not current.get("api_url"):
                raise HTTPException(
                    status_code=400,
                    detail="custom 提供商必须填写 API 地址（api_url）",
                )
            full = cfg.to_dict()
            full["llm"] = current
            cfg.update(full)
            _maybe_notify_bot(reload_llm=True)
            # 审计埋点：仅记录变更字段名（不含 api_key 等字段值）
            await record_audit(
                "config_update_llm", f"更新 LLM 配置，字段: {sorted(data.keys())}", request
            )
            return {"message": "LLM 配置已更新"}
        except HTTPException:
            raise
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception("配置更新失败")
            raise HTTPException(status_code=500, detail="内部错误，详见服务端日志")


@router.post("/llm/test", dependencies=[Depends(require_auth)])
async def test_llm_config(data: dict):
    """测试 LLM 连接"""
    manager = None
    try:
        cfg = _get_config_manager()
        current = cfg.llm.model_dump()
        for k, v in _filter_masked(data).items():
            if v is not None:
                current[k] = v

        if current.get("provider") == "custom" and not current.get("api_url"):
            raise HTTPException(
                status_code=400,
                detail="custom 提供商必须填写 API 地址（api_url）",
            )
        manager = LLMManager(LLMConfig(**current))
        available = await manager.check_availability()
        return {
            "available": available,
            "message": "LLM 连接正常" if available else "LLM 连接失败",
        }
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("LLM 连接测试失败")
        raise HTTPException(status_code=500, detail="内部错误，详见服务端日志")
    finally:
        if manager is not None:
            await manager.close()


@router.get("/onebot", dependencies=[Depends(require_auth)])
async def get_onebot_config():
    """获取 OneBot 连接配置（access_token 脱敏）"""
    data = _get_config_manager().onebot.model_dump()
    return _mask_sensitive(data)
