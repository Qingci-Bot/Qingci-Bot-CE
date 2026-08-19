"""插件市场功能测试

验证：
- MarketIndex 解析与校验（重复名/缺字段过滤）
- 版本对比（is_newer / semver 分段）
- MarketClient 索引缓存（TTL 内不重拉）与磁盘回退
- MarketManager 列表合并状态（installed/update_available）
- 安装/更新编排（卸载→安装）
- API 路由（列表/安装/刷新）
"""

import json
from pathlib import Path

import pytest

from bot.plugin.market import (
    MarketClient,
    MarketError,
    MarketIndex,
    MarketManager,
    is_newer,
)

SAMPLE_INDEX = {
    "name": "测试市场",
    "version": 1,
    "plugins": [
        {
            "name": "hello",
            "title": "Hello 示例",
            "description": "示例插件",
            "version": "1.2.0",
            "author": "tester",
            "type": "sdk",
            "icon": "👋",
            "homepage": "https://example.com/hello",
            "source": "https://example.com/hello.git",
            "mirror": "https://example.com/hello-mirror.git",
            "python_requires": ">=3.10",
            "tags": ["demo"],
            "requirements": ["qingci-plugin-sdk>=1.0"],
        },
        {
            "name": "echo",
            "title": "Echo",
            "description": "回显",
            "version": "2.0.0",
            "source": "https://example.com/echo.git",
        },
        {"name": "no-source"},  # 缺 source，应被过滤
        {"name": "hello", "source": "dup"},  # 重名，应被过滤
    ],
}


@pytest.fixture
def sample_index() -> MarketIndex:
    return MarketIndex(SAMPLE_INDEX)


# ---------- MarketIndex ----------


def test_index_parse_filters_invalid(sample_index):
    names = [p["name"] for p in sample_index.plugins]
    assert names == ["hello", "echo"]
    hello = sample_index.get("hello")
    assert hello["version"] == "1.2.0"
    assert hello["icon"] == "👋"
    assert hello["homepage"] == "https://example.com/hello"
    assert hello["requirements"] == ["qingci-plugin-sdk>=1.0"]
    assert hello["mirror"] == "https://example.com/hello-mirror.git"
    assert hello["python_requires"] == ">=3.10"
    # 缺省字段回退为空
    assert sample_index.get("echo")["icon"] == ""
    assert sample_index.get("echo")["mirror"] == ""
    assert sample_index.get("echo")["python_requires"] == ""
    assert sample_index.get("echo")["requirements"] == []
    assert sample_index.get("missing") is None


# ---------- 版本对比 ----------


def test_semver_compare():
    assert is_newer("1.2.0", "1.1.9")
    assert is_newer("2.0.0", "1.9.9")
    assert is_newer("1.2.3", "1.2.2")
    assert not is_newer("1.2.0", "1.2.0")
    assert not is_newer("1.2.0", "1.2.1")
    assert not is_newer("1.2", "1.2.0")  # 补丁缺省视为 0
    assert is_newer("1.2.3-beta", "1.2.2")  # 预发布版本号主版本更高仍判定更新
    assert not is_newer("1.2.3-beta", "1.2.3-alpha")  # 非数字段截断，视为相同
    assert not is_newer("v1.2.0", "1.2.0")  # v 前缀忽略非数字部分


# ---------- MarketClient ----------


async def test_client_cache(tmp_path: Path, monkeypatch):
    """TTL 内第二次 get_index 不重复拉取（命中内存缓存）"""
    fetches = {"n": 0}
    data = json.dumps(SAMPLE_INDEX, ensure_ascii=False).encode("utf-8")

    def fake_openurl(req, timeout=60):
        class Resp:
            def read(self):
                return data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fetches["n"] += 1
        return Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_openurl)
    client = MarketClient(url="https://fake.invalid/index.json", refresh_interval=3600)
    # 缓存目录隔离到 tmp_path
    monkeypatch.setattr("bot.plugin.market.data_root", lambda: tmp_path)

    i1 = await client.get_index()
    i2 = await client.get_index()
    assert i1 is i2
    assert fetches["n"] == 1  # 命中缓存，只拉一次


async def test_client_fallback_to_disk_cache(tmp_path: Path, monkeypatch):
    """远端拉取失败时回退磁盘缓存"""
    good = json.dumps(SAMPLE_INDEX, ensure_ascii=False).encode("utf-8")
    state = {"fail": False}

    def fake_openurl(req, timeout=60):
        if state["fail"]:
            raise OSError("network down")
        return _FakeResp(good)

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_openurl)
    monkeypatch.setattr("bot.plugin.market.data_root", lambda: tmp_path)
    client = MarketClient(url="https://fake.invalid/index.json", refresh_interval=0)

    i1 = await client.get_index()
    assert i1.get("hello") is not None

    # 网络故障：仍能通过磁盘缓存返回
    state["fail"] = True
    client.clear_cache()  # 清内存缓存，强制走磁盘
    i2 = await client.get_index()
    assert i2.get("hello") is not None


async def test_client_no_cache_fails(tmp_path: Path, monkeypatch):
    """无缓存且拉取失败时抛 MarketError"""
    import urllib.request

    def fail(req, timeout=60):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    monkeypatch.setattr("bot.plugin.market.data_root", lambda: tmp_path)
    client = MarketClient(url="https://fake.invalid/index.json", refresh_interval=0)
    with pytest.raises(MarketError):
        await client.get_index()


# ---------- MarketManager ----------


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_manager(tmp_path: Path, monkeypatch, data: bytes) -> MarketManager:
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=60: _FakeResp(data))
    monkeypatch.setattr("bot.plugin.market.data_root", lambda: tmp_path)
    return MarketManager(url="https://fake.invalid/index.json", refresh_interval=0)


class _FakePlugin:
    def __init__(self, name, version):
        self.name = name
        self.version = version


class _FakeManager:
    def __init__(self, plugins: dict[str, _FakePlugin]):
        self._plugins = plugins

    @property
    def plugins(self):
        return self._plugins

    def get(self, name):
        return self._plugins.get(name)

    async def unload(self, name):
        self._plugins.pop(name, None)

    async def install(self, bot, source, name=None):
        assert source  # source 传入
        self._plugins[name or "x"] = _FakePlugin(name or "x", "1.2.0")
        return True


class _FakeBot:
    def __init__(self, manager):
        self.plugin_manager = manager


async def test_list_market_status(tmp_path: Path, monkeypatch):
    """列表合并 installed/update_available 状态"""
    manager = _make_manager(tmp_path, monkeypatch, json.dumps(SAMPLE_INDEX).encode())
    fake_mgr = _FakeManager({"hello": _FakePlugin("hello", "1.0.0")})
    items = await manager.list_market(_FakeBot(fake_mgr))

    hello = next(i for i in items if i["name"] == "hello")
    assert hello["installed"] is True
    assert hello["installed_version"] == "1.0.0"
    assert hello["update_available"] is True  # 1.2.0 > 1.0.0

    echo = next(i for i in items if i["name"] == "echo")
    assert echo["installed"] is False
    assert echo["update_available"] is False

    # python_requires 兼容性（本机 3.10+ 均满足 >=3.10）
    assert hello["compatible"] is True


async def test_install_unloads_then_installs(tmp_path: Path, monkeypatch):
    """安装已加载插件：先卸载再安装"""
    manager = _make_manager(tmp_path, monkeypatch, json.dumps(SAMPLE_INDEX).encode())
    fake_mgr = _FakeManager({"hello": _FakePlugin("hello", "1.0.0")})
    bot = _FakeBot(fake_mgr)

    await manager.install(bot, "hello")
    assert "hello" in fake_mgr._plugins
    assert fake_mgr._plugins["hello"].version == "1.2.0"


async def test_install_falls_back_to_mirror(tmp_path: Path, monkeypatch):
    """主地址 source 失败时回退到备用地址 mirror"""
    manager = _make_manager(tmp_path, monkeypatch, json.dumps(SAMPLE_INDEX).encode())
    calls: list[str] = []

    class _FlakyManager(_FakeManager):
        async def install(self, bot, source, name=None):
            calls.append(source)
            if "mirror" not in source:
                return False
            self._plugins[name or "x"] = _FakePlugin(name or "x", "1.2.0")
            return True

    fake_mgr = _FlakyManager({"hello": _FakePlugin("hello", "1.0.0")})
    bot = _FakeBot(fake_mgr)

    assert await manager.install(bot, "hello") is True
    assert calls == ["https://example.com/hello.git", "https://example.com/hello-mirror.git"]
    assert fake_mgr._plugins["hello"].version == "1.2.0"


async def test_install_all_sources_fail(tmp_path: Path, monkeypatch):
    """source 与 mirror 均失败时抛 MarketError"""
    manager = _make_manager(tmp_path, monkeypatch, json.dumps(SAMPLE_INDEX).encode())

    class _FailingManager(_FakeManager):
        async def install(self, bot, source, name=None):
            return False

    fake_mgr = _FailingManager({"hello": _FakePlugin("hello", "1.0.0")})
    bot = _FakeBot(fake_mgr)

    with pytest.raises(MarketError, match="已尝试 2 个地址"):
        await manager.install(bot, "hello")


async def test_install_unknown_plugin(tmp_path: Path, monkeypatch):
    """安装不存在的插件抛 MarketError"""
    manager = _make_manager(tmp_path, monkeypatch, json.dumps(SAMPLE_INDEX).encode())
    with pytest.raises(MarketError):
        await manager.install(_FakeBot(_FakeManager({})), "nope")


async def test_refresh_force(tmp_path: Path, monkeypatch):
    """refresh 强制绕过 TTL"""
    data = json.dumps(SAMPLE_INDEX).encode()
    manager = _make_manager(tmp_path, monkeypatch, data)
    index = await manager.refresh()
    assert index.get("hello") is not None


async def test_market_info(tmp_path: Path, monkeypatch):
    """market_info 返回名称/插件数/索引更新时间"""
    data = json.dumps(SAMPLE_INDEX).encode()
    manager = _make_manager(tmp_path, monkeypatch, data)
    info = await manager.market_info()
    assert info["name"] == "测试市场"
    assert info["plugin_count"] == 2
    assert info["fetched_at"] > 0  # 本次会话成功拉取过
