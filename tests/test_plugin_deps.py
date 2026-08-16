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
    calls: list[list[str]] = []

    async def fake_install(reqs):
        calls.append(reqs)
        return True

    monkeypatch.setattr(deps, "_install", fake_install)

    got = asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert got == ["requests"]
    assert len(calls) == 1
    # deps 目录已注入 sys.path
    assert str(deps.deps_dir()) in sys.path

    # 内容未变 → 幂等跳过（不再触发安装）
    got2 = asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert got2 == ["requests"]
    assert len(calls) == 1

    # requirements 变更 → 重新安装
    (plugin_dir / "requirements.txt").write_text("requests\nhttpx\n", encoding="utf-8")
    asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert len(calls) == 2
    assert calls[-1] == ["requests", "httpx"]


def test_ensure_dependencies_install_failure(tmp_path, monkeypatch):
    set_data_root(tmp_path)
    plugin_dir = _make_plugin(tmp_path, "requests\n")

    async def fake_install(reqs):
        return False

    monkeypatch.setattr(deps, "_install", fake_install)

    got = asyncio.run(deps.ensure_dependencies(plugin_dir))
    assert got == ["requests"]
    # 安装失败不写 marker，下次会重试
    assert not deps._marker_path(plugin_dir.name).exists()
