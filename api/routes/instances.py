"""实例管理接口

GET    /api/instances           列出全部实例（含当前运行实例标记）
POST   /api/instances           创建实例
DELETE /api/instances/{name}    删除实例
POST   /api/instances/{name}/start  重启到指定实例
"""

from __future__ import annotations

import logging
import os
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.audit import record_audit
from api.auth import require_auth
from bot.instances import (
    create_instance,
    delete_instance,
    get_instance,
    instance_path,
    is_valid_name,
    list_instances,
    rename_instance,
)
from bot.paths import data_root
from desktop.relaunch import spawn_relaunch

logger = logging.getLogger("qingci-bot.api.instances")

router = APIRouter()


def _current_instance_name() -> str | None:
    """推断当前运行实例名：data_root() 归属到 instances/<name>/data 时返回其名"""
    root = data_root().resolve()
    for inst in list_instances():
        if (instance_path(inst.name) / "data").resolve() == root:
            return inst.name
    return None


def _build_start_args(name: str) -> list[str]:
    """构造重启到指定实例的应用参数（保留桌面/无Bot/监听地址/端口等标志）"""
    args = ["--instance", name]
    # 保留桌面模式（frozen windowed 下 sys.argv 无 --desktop，需用显式标志判断）
    from bot.paths import is_desktop

    if is_desktop():
        args.append("--desktop")
    if "--no-bot" in sys.argv:
        args.append("--no-bot")
    for flag in ("--host", "--port"):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            if i + 1 < len(sys.argv):
                args += [flag, sys.argv[i + 1]]
    return args


@router.get("", dependencies=[Depends(require_auth)])
async def get_instances() -> list[dict]:
    """列出全部实例，running 标记当前运行实例"""
    current = _current_instance_name()
    result = []
    for inst in list_instances():
        info = inst.to_dict()
        info["running"] = inst.name == current
        result.append(info)
    return result


class CreateInstanceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    port: int | None = Field(default=None, ge=1024, le=65535)


@router.post("", dependencies=[Depends(require_auth)], status_code=201)
async def create_new_instance(req: CreateInstanceRequest) -> dict:
    """创建实例（config.yaml 模板 + plugins/ + data/）"""
    try:
        inst = create_instance(
            name=req.name,
            description=req.description,
            port=req.port,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await record_audit("instance.create", f"创建实例 {req.name}")
    return inst.to_dict()


@router.delete("/{name}", dependencies=[Depends(require_auth)])
async def remove_instance(name: str) -> dict:
    """删除实例（含数据）"""
    if name == _current_instance_name():
        raise HTTPException(status_code=400, detail="不能删除正在运行的实例") from None
    if not delete_instance(name):
        raise HTTPException(status_code=404, detail=f"实例不存在: {name}") from None
    await record_audit("instance.delete", f"删除实例 {name}")
    return {"ok": True}


class RenameInstanceRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=64)


@router.put("/{name}", dependencies=[Depends(require_auth)])
async def rename_existing_instance(name: str, req: RenameInstanceRequest) -> dict:
    """重命名实例（重命名目录 + 更新元数据）

    支持重命名运行中的实例：当前进程持有打开的 DB，Windows 拒绝重命名含打开
    句柄的目录，因此改为立即退出，由分离的助手进程在旧进程退出（文件锁释放）
    后先改目录名、再拉起新实例。该分支不会返回响应。
    """
    if get_instance(name) is None:
        raise HTTPException(status_code=404, detail=f"实例不存在: {name}") from None
    if not is_valid_name(req.new_name):
        raise HTTPException(status_code=400, detail=f"非法实例名: {req.new_name!r}") from None
    if get_instance(req.new_name) is not None:
        raise HTTPException(status_code=400, detail=f"实例已存在: {req.new_name}") from None

    is_running = name == _current_instance_name()
    await record_audit("instance.rename", f"实例 {name} 改名为 {req.new_name}")

    if is_running:
        spawn_relaunch(["--rename-dir", name, req.new_name] + _build_start_args(req.new_name))
        os._exit(0)  # noqa: PLR1722 — 主动终止进程完成改名切换
        return {}  # pragma: no cover — 永不返回

    rename_instance(name, req.new_name)
    info = get_instance(req.new_name).to_dict()  # type: ignore[union-attr]  # 刚改名，必然存在
    info["running"] = False
    return info


@router.post("/{name}/start", dependencies=[Depends(require_auth)])
async def start_instance(name: str) -> dict:
    """重启到指定实例（当前进程退出，由分离的助手进程在旧进程退出后拉起目标实例）"""
    if get_instance(name) is None:
        raise HTTPException(status_code=404, detail=f"实例不存在: {name}") from None
    if name == _current_instance_name():
        raise HTTPException(status_code=400, detail="已运行于该实例") from None

    await record_audit("instance.start", f"切换到实例 {name}")
    spawn_relaunch(_build_start_args(name))

    # 立即退出当前进程，释放实例互斥量，让新进程接管
    os._exit(0)  # noqa: PLR1722 — 主动终止进程完成切换
    return {"ok": True}  # pragma: no cover — 永不返回
