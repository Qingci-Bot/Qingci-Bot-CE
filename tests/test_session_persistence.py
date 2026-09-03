"""会话状态持久化测试 — 覆盖 save_snapshot/restore_snapshot 的落盘、原子写与容错"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from bot.core.session_persistence import restore_snapshot, save_snapshot, session_state_path
from bot.core.session_state import SessionStateManager


@pytest.fixture
def tmp_data_dir():
    """临时数据目录（monkeypatch session_persistence.data_root）"""
    import shutil

    import bot.core.session_persistence as _sp

    tmp = tempfile.mkdtemp()
    original = _sp.data_root
    _sp.data_root = lambda: Path(tmp)
    yield Path(tmp)
    _sp.data_root = original
    shutil.rmtree(tmp, ignore_errors=True)


def _make_manager() -> SessionStateManager:
    return SessionStateManager()


@pytest.mark.asyncio
async def test_round_trip_preserves_non_expiring_keys(tmp_data_dir):
    """存档-恢复：ttl=0 键保留，ttl>0 键被 serialize 丢弃"""
    mgr = _make_manager()
    await mgr.set("step", "waiting", user_id=1)  # ttl=0
    await mgr.set("temp", "x", ttl=60, user_id=1)  # ttl>0 应被丢弃
    path = tmp_data_dir / "s.json"

    saved = await save_snapshot(mgr, str(path))
    assert saved >= 1

    fresh = _make_manager()
    restored = await restore_snapshot(fresh, str(path))
    assert restored >= 1
    val = await fresh.get("step", user_id=1)
    assert val == "waiting"
    # ttl>0 键未持久化
    temp_val = await fresh.get("temp", "default", user_id=1)
    assert temp_val == "default"


@pytest.mark.asyncio
async def test_atomic_write_no_tmp_residue(tmp_data_dir):
    """原子写：目标文件存在且无 .tmp 残留"""
    mgr = _make_manager()
    await mgr.set("k", "v", user_id=1)
    path = tmp_data_dir / "s.json"
    await save_snapshot(mgr, str(path))
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


@pytest.mark.asyncio
async def test_restore_corrupt_json_returns_zero_and_backs_up(tmp_data_dir):
    """损坏文件：restore 返回 0，原文件转 .bak，不抛异常"""
    path = tmp_data_dir / "s.json"
    path.write_text("{ not valid json !!!", encoding="utf-8")
    mgr = _make_manager()
    restored = await restore_snapshot(mgr, str(path))
    assert restored == 0
    assert not path.exists()
    assert path.with_suffix(path.suffix + ".bak").exists()


@pytest.mark.asyncio
async def test_restore_missing_file_returns_zero(tmp_data_dir):
    """缺文件：restore 返回 0"""
    mgr = _make_manager()
    path = tmp_data_dir / "nonexistent.json"
    assert await restore_snapshot(mgr, str(path)) == 0


@pytest.mark.asyncio
async def test_restore_non_dict_returns_zero(tmp_data_dir):
    """快照内容非法（如数组）：restore 返回 0 并备份"""
    path = tmp_data_dir / "s.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    mgr = _make_manager()
    assert await restore_snapshot(mgr, str(path)) == 0
    assert path.with_suffix(path.suffix + ".bak").exists()


@pytest.mark.asyncio
async def test_concurrent_save_no_error(tmp_data_dir):
    """并发保存：模块级 asyncio.Lock 串行化，文件仍可解析"""
    mgr = _make_manager()
    await mgr.set("a", "1", user_id=1)
    await mgr.set("b", "2", user_id=2)
    path = tmp_data_dir / "s.json"

    results = await asyncio.gather(
        save_snapshot(mgr, str(path)),
        save_snapshot(mgr, str(path)),
        save_snapshot(mgr, str(path)),
    )
    assert all(isinstance(r, int) for r in results)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_session_state_path_defaults_to_data_root(tmp_data_dir):
    """默认路径在 data_root() 下"""
    assert session_state_path().parent == Path(tmp_data_dir)
