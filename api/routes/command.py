"""命令管理接口

提供命令列表查看、冲突检测、禁用/启用和优先级调整。
"""

import logging

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field

from bot.core.bot import get_bot as _get_bot
from api.auth import require_auth
from api.audit import record_audit

logger = logging.getLogger("qingci-bot.api.command")

router = APIRouter()


class CommandUpdate(BaseModel):
    """命令更新请求体"""
    disabled: bool | None = Field(None, description="是否禁用该命令")
    priority: int | None = Field(None, description="优先级（越小越先执行）", ge=0, le=100)


def _get_bot_instance():
    try:
        return _get_bot()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Bot 未初始化，请先启动 Bot 服务")


@router.get("/conflicts", dependencies=[Depends(require_auth)])
async def list_commands():
    """列出所有已注册命令及其冲突信息

    返回每个命令的所属插件、优先级、禁用状态、事件类型，
    并标记是否存在同名命令冲突。
    """
    bot = _get_bot_instance()
    pm = bot.plugin_manager

    # 收集所有 Matcher（含 disabled），按命令名分组
    commands: dict[str, list[dict]] = {}
    for plugin in pm.plugins.values():
        for m in plugin.matchers:
            cmd = m.meta.get("command") if m.meta else None
            if not cmd:
                continue
            entry = {
                "command": cmd,
                "plugin": m.owner or plugin.name,
                "priority": m.priority,
                "disabled": m.disabled,
                "event_type": m.event_type,
                "description": m.meta.get("description", ""),
            }
            commands.setdefault(cmd, []).append(entry)

    # 构建响应：标记冲突
    result = []
    for cmd, entries in sorted(commands.items()):
        has_conflict = len(entries) > 1
        for e in entries:
            e["has_conflict"] = has_conflict
            result.append(e)

    return result


@router.put("/{owner}/{command}", dependencies=[Depends(require_auth)])
async def update_command(owner: str, command: str, body: CommandUpdate, request: Request):
    """更新命令状态（禁用/启用/优先级）

    通过 owner（插件名）和 command（命令名）定位 Matcher。
    """
    if body.disabled is None and body.priority is None:
        raise HTTPException(status_code=400, detail="至少需要提供 disabled 或 priority 字段")

    bot = _get_bot_instance()
    pm = bot.plugin_manager

    updated = False
    for plugin in pm.plugins.values():
        for m in plugin.matchers:
            if m.owner != owner or m.meta.get("command") != command:
                continue
            if body.disabled is not None:
                m.disabled = body.disabled
            if body.priority is not None:
                m.priority = body.priority
            updated = True
            pm._invalidate_matchers_cache()
            action = "禁用" if body.disabled else ("启用" if body.disabled is False else "更新")
            await record_audit(
                "command.update",
                f"{action}命令 {owner}/{command} priority={body.priority}",
                request,
            )
            logger.info(f"命令已更新: {owner}/{command} disabled={body.disabled} priority={body.priority}")
            return {
                "command": command,
                "plugin": owner,
                "disabled": m.disabled,
                "priority": m.priority,
            }

    raise HTTPException(status_code=404, detail=f"命令 {owner}/{command} 不存在")