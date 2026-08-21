"""外部插件依赖管理（bot/plugin/deps.py）单元测试"""

import asyncio
import sys
from pathlib import Path

import bot.plugin.deps as deps
from bot.paths import set_data_root


def _make_plugin(tmp_path: Path, reqs_text: str) -> Path:
    d = tmp_path / "dummy_plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "requirements.txt").write_text(reqs_text, encoding="utf-8")
    return d


def test_read_requirements_from_file(tmp_path):
    d = _make_plugin(tmp_path, "# comment\nrequests>=2.0\n\nhttpx\n")
    assert deps.read_requirements(d) == ["requests>=2.0", "httpx"]


def test_read_requirements_from_plugin_json(tmp_path):
    d = tmp_path / "p2"
    d.mkdir()
    (d / "plugin.json").write_text(
        '{"name": "p2", "requirements": ["aiohttp", "pillow"]}', encoding="utf-8"
    )
    assert deps.read_requirements(d) == ["aiohttp", "pillow"]


def test_read_requirements_empty(tmp_path):
    d = tmp_path / "p3"
    d.mkdir()
    assert deps.read_requirements(d) == []


def test_ensure_dependencies_idempotent(tmp_path, monkeypatch):
    set_data_root(tmp_path)
    plugin_dir = _make_plugin(tmp_path, "requests\n")
    calls: list[tuple[list[str], Path]] = []

    async def fake_install(reqs, target):
        calls.append((reqs, target))
        return True

    monkeypatch.setattr(deps, "_install", fake_install)

    got = asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert got == ["requests"]
    assert len(calls) == 1
    # 安装目标是该插件专属的 deps 子目录（非共享平铺目录）
    assert calls[0][1] == deps.deps_dir("dummy_plugin")
    # 该插件专属的 deps 子目录已注入 sys.path
    assert str(deps.deps_dir("dummy_plugin")) in sys.path

    # 内容未变 → 幂等跳过（不再触发安装）
    got2 = asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert got2 == ["requests"]
    assert len(calls) == 1

    # requirements 变更 → 重新安装
    (plugin_dir / "requirements.txt").write_text("requests\nhttpx\n", encoding="utf-8")
    asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert len(calls) == 2
    assert calls[-1][0] == ["requests", "httpx"]


def test_ensure_dependencies_per_plugin_isolation(tmp_path, monkeypatch):
    """不同插件安装到各自独立的 deps 子目录，互不共享"""
    set_data_root(tmp_path)
    plugin_a = tmp_path / "plugin_a"
    plugin_a.mkdir(parents=True, exist_ok=True)
    (plugin_a / "requirements.txt").write_text("requests\n", encoding="utf-8")
    plugin_b = tmp_path / "plugin_b"
    plugin_b.mkdir(parents=True, exist_ok=True)
    (plugin_b / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")

    targets: list[Path] = []

    async def fake_install(reqs, target):
        targets.append(target)
        return True

    monkeypatch.setattr(deps, "_install", fake_install)

    asyncio.run(deps.ensure_dependencies(plugin_a))
    asyncio.run(deps.ensure_dependencies(plugin_b))

    assert targets == [deps.deps_dir("plugin_a"), deps.deps_dir("plugin_b")]
    assert targets[0] != targets[1]
    # 各自目录均注入 sys.path
    assert str(deps.deps_dir("plugin_a")) in sys.path
    assert str(deps.deps_dir("plugin_b")) in sys.path


def test_ensure_dependencies_install_failure(tmp_path, monkeypatch):
    set_data_root(tmp_path)
    plugin_dir = _make_plugin(tmp_path, "requests\n")

    async def fake_install(reqs, target):
        return False

    monkeypatch.setattr(deps, "_install", fake_install)

    got = asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert got == ["requests"]
    # 安装失败不写 marker，下次会重试
    assert not deps._marker_path(plugin_dir.name).exists()


def test_install_subprocess_timeout_kills_process(tmp_path, monkeypatch):
    """communicate 超时（默认 300s）必须 kill 进程并返回 False，不挂死"""

    class FakeProc:
        def __init__(self):
            self.returncode = None
            self.killed = False

        async def communicate(self):
            await asyncio.sleep(3600)  # 永不返回，触发 wait_for 超时

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return -9

    fake = FakeProc()

    async def fake_create(*args, **kwargs):
        return fake

    monkeypatch.setattr(deps.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(deps, "_INSTALL_TIMEOUT", 0.05)

    ok = asyncio.run(deps._install_subprocess(tmp_path, ["requests"]))
    assert ok is False
    assert fake.killed is True
