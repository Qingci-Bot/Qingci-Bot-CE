"""实例管理：多实例（完全自包含目录）的扫描、创建、删除与元数据读写

每个实例 = instances/<name>/ 自包含目录：
    instance.json   # 元数据：名称、端口、描述、创建时间
    config.yaml     # 该实例专属配置
    plugins/        # 该实例专属插件代码
    data/           # 可写数据根（DB/日志/插件数据，即 data_root()）

实例与运行进程解耦：本模块只负责磁盘上的实例目录管理，
不持有运行状态。运行实例判定由上层（当前进程 data_root 归属）提供。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .paths import app_root, instances_dir

INSTANCE_META = "instance.json"
DEFAULT_PORT = 8080
DEFAULT_INSTANCE_NAME = "default"


@dataclass
class InstanceInfo:
    """实例的磁盘形态信息"""

    name: str
    port: int = DEFAULT_PORT
    description: str = ""
    created_at: str = ""
    running: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(instance_path(self.name))
        return d


def instance_path(name: str) -> Path:
    """返回实例目录（instances/<name>）"""
    return instances_dir() / name


def is_valid_name(name: str) -> bool:
    """实例名校验：仅允许字母/数字/下划线/连字符，禁止路径穿越与隐藏目录"""
    return bool(name) and all(c.isalnum() or c in "-_" for c in name) and not name.startswith(".")


def _read_meta(path: Path) -> dict:
    meta_file = path / INSTANCE_META
    if meta_file.is_file():
        try:
            data: dict = json.loads(meta_file.read_text(encoding="utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _write_meta(path: Path, meta: dict) -> None:
    (path / INSTANCE_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_instances() -> list[InstanceInfo]:
    """扫描 instances/ 目录，返回全部实例（按名称排序）"""
    root = instances_dir()
    if not root.is_dir():
        return []
    result: list[InstanceInfo] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        meta = _read_meta(entry)
        result.append(
            InstanceInfo(
                name=entry.name,
                port=int(meta.get("port", DEFAULT_PORT)),
                description=meta.get("description", ""),
                created_at=meta.get("created_at", ""),
            )
        )
    return result


def get_instance(name: str) -> InstanceInfo | None:
    path = instance_path(name)
    if not path.is_dir() or not is_valid_name(name):
        return None
    meta = _read_meta(path)
    return InstanceInfo(
        name=name,
        port=int(meta.get("port", DEFAULT_PORT)),
        description=meta.get("description", ""),
        created_at=meta.get("created_at", ""),
    )


def _next_free_port() -> int:
    """从 8080 起分配未被占用（DB 层面）的端口"""
    used = {inst.port for inst in list_instances()}
    port = DEFAULT_PORT
    while port in used:
        port += 1
    return port


def create_instance(
    name: str,
    description: str = "",
    port: int | None = None,
    template: Path | None = None,
) -> InstanceInfo:
    """创建实例目录（含 config.yaml 模板、plugins/、data/）

    Args:
        name: 实例名（instances/<name>）
        description: 描述
        port: 端口；缺省自动分配（≥8081）
        template: config.yaml 模板来源；缺省用 app_root()/config.example.yaml

    Returns:
        新建的实例信息

    Raises:
        ValueError: 名称非法或实例已存在
    """
    if not is_valid_name(name):
        raise ValueError(f"非法实例名: {name!r}")
    path = instance_path(name)
    if path.exists():
        raise ValueError(f"实例已存在: {name}")

    # 端口须在目录创建前分配：list_instances() 会把已建目录按默认端口计入，
    # 若先 mkdir 再分配会把当前实例误判为占用 8080，导致端口 +1 偏移。
    port = port or _next_free_port()

    path.mkdir(parents=True, exist_ok=True)

    # config.yaml：从模板复制
    src = template or (app_root() / "config.example.yaml")
    if src.is_file():
        (path / "config.yaml").write_bytes(src.read_bytes())

    # plugins/ 与 data/ 目录
    (path / "plugins").mkdir(exist_ok=True)
    (path / "data").mkdir(exist_ok=True)

    created_at = datetime.now().isoformat(timespec="seconds")
    meta = {
        "name": name,
        "port": port,
        "description": description,
        "created_at": created_at,
    }
    _write_meta(path, meta)
    return InstanceInfo(
        name=name,
        port=port,
        description=description,
        created_at=created_at,
    )


def delete_instance(name: str) -> bool:
    """删除实例目录（含数据）。返回 False 表示实例不存在。"""
    path = instance_path(name)
    if not path.is_dir():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def rename_instance(old_name: str, new_name: str) -> InstanceInfo:
    """将实例 old_name 改名为 new_name（重命名目录 + 更新元数据）

    Args:
        old_name: 现有实例名
        new_name: 新实例名（须合法且不与已有实例冲突）

    Returns:
        改名后的实例信息

    Raises:
        ValueError: 名称非法、实例不存在或新名已存在
    """
    if not is_valid_name(new_name):
        raise ValueError(f"非法实例名: {new_name!r}")
    if get_instance(old_name) is None:
        raise ValueError(f"实例不存在: {old_name}")
    new_path = instance_path(new_name)
    if new_path.exists():
        raise ValueError(f"实例已存在: {new_name}")

    old_path = instance_path(old_name)
    old_path.rename(new_path)

    # 更新 instance.json 中的 name 字段（无元数据文件时跳过）
    meta = _read_meta(new_path)
    if meta:
        meta["name"] = new_name
        _write_meta(new_path, meta)

    return get_instance(new_name)  # type: ignore[return-value]  # 刚改名，必然存在


def default_instance_name() -> str | None:
    """返回默认实例名：显式 default 实例优先，否则取名称排序后的第一个；无实例返回 None"""
    insts = list_instances()
    if not insts:
        return None
    for inst in insts:
        if inst.name == DEFAULT_INSTANCE_NAME:
            return inst.name
    return insts[0].name


def ensure_default_instance() -> InstanceInfo:
    """确保至少存在一个实例，返回默认实例；无任何实例时自动创建 default"""
    name = default_instance_name()
    if name is None:
        return create_instance(DEFAULT_INSTANCE_NAME, description="默认实例")
    inst = get_instance(name)
    return inst  # type: ignore[return-value]  # 由 default_instance_name 保证存在


def default_config_path() -> Path:
    """返回默认实例的 config.yaml 路径（无全局模式）

    供 API 鉴权/配置接口在未显式指定 --config 时定位配置，保证配置始终落在
    实例自包含目录内，绝不回退到根级 app_root()/config.yaml。
    """
    name = default_instance_name()
    if name:
        return instance_path(name) / "config.yaml"
    return app_root() / "config.yaml"
