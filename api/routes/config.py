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
        if not available:
            detail = getattr(manager, "last_error", "") or "未知错误"
            logger.warning(f"LLM 连接测试失败: {detail}")
        return {
            "available": available,
            "message": "LLM 连接正常" if available else f"LLM 连接失败：{detail}",
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


@router.post("/llm/models", dependencies=[Depends(require_auth)])
async def list_llm_models(data: dict):
    """查询提供商可用模型列表（按 provider 调用对应模型列表 API）

    请求体与 /llm/test 一致（provider / api_url / api_key / model）。
    支持：
    - OpenAI 兼容（openai/deepseek/siliconflow/custom）: GET {api_url}/models
    - Ollama: GET {base}/api/tags
    - Claude: GET https://api.anthropic.com/v1/models
    - Gemini: GET https://generativelanguage.googleapis.com/v1beta/models
    失败返回 400 并携带具体错误信息，便于前端直接展示。
    """
    import httpx

    cfg = _get_config_manager()
    current = cfg.llm.model_dump()
    for k, v in _filter_masked(data).items():
        if v is not None:
            current[k] = v

    provider = current.get("provider", "")
    api_url = (current.get("api_url") or "").rstrip("/")
    api_key = current.get("api_key") or ""

    async def _fetch(url: str, headers: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()

    try:
        if provider == "ollama":
            base = api_url or "http://localhost:11434"
            payload = await _fetch(f"{base}/api/tags")
            models = [
                m.get("name", "") for m in payload.get("models", []) if m.get("name")
            ]
        elif provider == "claude":
            base = api_url or "https://api.anthropic.com/v1"
            payload = await _fetch(
                f"{base}/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            models = [
                m.get("id", "") for m in payload.get("data", []) if m.get("id")
            ]
        elif provider == "gemini":
            if not api_key:
                raise ValueError("Gemini 查询模型列表需要填写 API Key")
            payload = await _fetch(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
            )
            models = [
                m.get("name", "") for m in payload.get("models", []) if m.get("name")
            ]
        else:
            # OpenAI 兼容协议（openai/deepseek/siliconflow/custom）
            if not api_url:
                raise ValueError(f"provider {provider} 需要填写 API 地址（api_url）")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            payload = await _fetch(f"{api_url}/models", headers=headers)
            models = [
                m.get("id", "") for m in payload.get("data", []) if m.get("id")
            ]
        return {"models": sorted(set(models))}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"查询模型列表失败: provider={provider}, error={e}")
        raise HTTPException(status_code=400, detail=f"查询模型列表失败：{e}")


@router.get("/onebot", dependencies=[Depends(require_auth)])
async def get_onebot_config():
    """获取 OneBot 连接配置（access_token 脱敏）"""
    data = _get_config_manager().onebot.model_dump()
    return _mask_sensitive(data)


# ============ 配置引导向导 ============

@router.get("/wizard/status")
async def get_wizard_status():
    """检查是否需要引导配置（免鉴权，首次启动时 config.yaml 可能不存在）"""
    try:
        cfg = _get_config_manager()
    except Exception:
        return {"needs_setup": True, "reason": "config_load_failed"}

    # 判断：api_key 为空 且 没有 admin_users 时，视为未配置
    api_key = cfg.config.api_key or ""
    llm_api_key = cfg.llm.api_key or ""
    admin_users = cfg.bot.admin_users or []

    needs_setup = not api_key and not llm_api_key and not admin_users
    return {
        "needs_setup": needs_setup,
        "config_exists": cfg._path.exists(),
        "has_api_key": bool(api_key),
        "has_llm_key": bool(llm_api_key),
        "has_admin_users": bool(admin_users),
    }


@router.post("/wizard")
async def complete_wizard(data: dict):
    """完成初始配置引导（免鉴权，仅首次使用）

    接受字段：provider, api_key, model, admin_qq, onebot_port
    """
    async with _get_config_lock():
        try:
            cfg = _get_config_manager()
            current = cfg.to_dict()

            provider = str(data.get("provider", "") or "deepseek").strip()
            api_key = str(data.get("api_key", "") or "").strip()
            admin_qq = data.get("admin_qq")
            onebot_port = data.get("onebot_port")

            if not api_key:
                raise HTTPException(status_code=400, detail="API Key 不能为空")

            # 应用提供商预设
            preset = LLM_PROVIDER_PRESETS.get(provider, LLM_PROVIDER_PRESETS["deepseek"])
            current["llm"]["provider"] = provider
            current["llm"]["api_key"] = api_key
            current["llm"]["api_url"] = preset["api_url"]
            current["llm"]["model"] = preset["model"]

            # 管理员 QQ
            if admin_qq is not None:
                try:
                    qq = int(admin_qq)
                    if qq > 0:
                        current["bot"]["admin_users"] = [qq]
                except (ValueError, TypeError):
                    raise HTTPException(status_code=400, detail="管理员 QQ 号格式无效")

            # OneBot 端口
            if onebot_port is not None:
                try:
                    port = int(onebot_port)
                    if 1024 <= port <= 65535:
                        current["onebot"]["port"] = port
                except (ValueError, TypeError):
                    pass

            cfg.update(current)
            _maybe_notify_bot(reload_llm=True)

            return {
                "message": "初始配置完成",
                "provider": provider,
                "model": preset["model"],
            }
        except HTTPException:
            raise
        except Exception:
            logger.exception("初始配置向导失败")
            raise HTTPException(status_code=500, detail="内部错误，详见服务端日志")
