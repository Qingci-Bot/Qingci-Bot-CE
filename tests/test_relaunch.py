"""跨进程重启助手测试"""

import subprocess
import sys

import pytest

import bot.instances as inst_mod
import desktop.py.relaunch as relaunch


@pytest.fixture(autouse=True)
def _redirect_instances(tmp_path, monkeypatch):
    """将实例目录重定向到临时目录"""
    monkeypatch.setattr(inst_mod, "instances_dir", lambda: tmp_path)
    return tmp_path


def _make_launcher(monkeypatch):
    """捕获 relaunch 对 subprocess.Popen 的调用"""
    calls = []
    monkeypatch.setattr(relaunch.subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    return calls


def test_not_helper_mode_returns_false(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["qingci-bot", "--instance", "default"])
    assert relaunch.run_helper_if_requested() is False


def test_helper_renames_then_launches(monkeypatch, tmp_path):
    inst_mod.create_instance("old")

    # 旧进程用一个已退出的子进程（pid 已失效），使助手无需等待
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = dead.pid
    dead.wait()
    calls = _make_launcher(monkeypatch)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qingci-bot",
            "--relaunch-wait",
            str(pid),
            "--rename-dir",
            "old",
            "new",
            "--instance",
            "new",
        ],
    )

    assert relaunch.run_helper_if_requested() is True
    assert inst_mod.get_instance("old") is None
    assert inst_mod.get_instance("new") is not None
    assert calls and "new" in " ".join(calls[0])


def test_helper_without_rename_still_launches(monkeypatch):
    inst_mod.create_instance("keep")

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = dead.pid
    dead.wait()
    calls = _make_launcher(monkeypatch)

    monkeypatch.setattr(
        sys, "argv", ["qingci-bot", "--relaunch-wait", str(pid), "--instance", "keep"]
    )
    assert relaunch.run_helper_if_requested() is True
    assert inst_mod.get_instance("keep") is not None
    assert calls and "keep" in " ".join(calls[0])


def test_rename_failure_does_not_block_launch(monkeypatch, tmp_path):
    # 源实例不存在，改名抛错，但助手仍应拉起目标进程
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = dead.pid
    dead.wait()
    calls = _make_launcher(monkeypatch)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qingci-bot",
            "--relaunch-wait",
            str(pid),
            "--rename-dir",
            "ghost",
            "new",
            "--instance",
            "new",
        ],
    )
    assert relaunch.run_helper_if_requested() is True
    assert calls, "改名失败不应阻止拉起目标实例"
