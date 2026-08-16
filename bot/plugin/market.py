"""插件市场 — 集中索引 + 一键安装/更新编排

市场索引（index.json）由官方仓库 Qingci-Bot/Plugin-Market 维护，
格式：

    {
      "name": "Qingci-Bot 插件市场",
      "version": 1,
      "plugins": [
        {
          "name": "hello",                  # 插件名（与插件内 name 一致，唯一）
          "title": "Hello 示例插件",          # 展示名
          "description": "演示插件",         # 简介
          "version": "1.0.0",               # 索引版本（对比已装版本判断可更新）
          "author": "Qingci-Bot",
          "type": "sdk",                    # sdk / builtin
          "source": "https://...",          # 安装来源（git 仓库 / HTTP 归档 / 本地路径）
          "tags": ["demo"],
          "updated_at": "2026-08-16"
        }
      ]
    }

工作流程：
    MarketClient.fetch_index()  → 拉取 + TTL 缓存 index.json
    MarketManager.list_market() → 合并已装/未装/可更新状态
    MarketManager.install()     → 调 PluginManager.install(bot, source)
    MarketManager.update()      → 已加载则先卸载，再 install（覆盖重装）

索引缓存落在 data_root()/market/index.json + index.meta.json（时间戳），
拉取失败回退磁盘缓存（网络离线仍可浏览）。
"""

import asyncio
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

from ..paths import data_root

logger = logging.getLogger("qingci-bot.market")

# 官方插件市场默认索引仓库（AtomGit）
DEFAULT_MARKET_URL = "https://atomgit.com/Qingci-Bot/Plugin-Market.git"
# 索引 TTL（秒）：缓存有效期内不重复拉取
DEFAULT_REFRESH_INTERVAL = 3600.0
# 索引版本号（schema 演进时递增）
INDEX_VERSION = 1


class MarketError(Exception):
    """插件市场错误"""


class MarketIndex:
    """市场索引（解析 + 校验）"""

    def __init__(self, data: dict):
        self.name: str = str(data.get("name", "插件市场"))
        self.version: int = int(data.get("version", 1) or 1)
        self.plugins: list[dict] = []
        self._parse_plugins(data.get("plugins", []))

    def _parse_plugins(self, raw: list) -> None:
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            source = str(item.get("source", "")).strip()
            if not name or not source:
                logger.warning(f"市场索引条目缺少 name/source: {item}")
                continue
            if name in seen:
                logger.warning(f"市场索引插件名重复: {name}，忽略后项")
                continue
            seen.add(name)
            self.plugins.append(
                {
                    "name": name,
                    "title": str(item.get("title") or name),
                    "description": str(item.get("description", "")),
                    "version": str(item.get("version", "0.0.0")),
                    "author": str(item.get("author", "")),
                    "type": str(item.get("type", "sdk")),
                    "source": source,
                    "icon": str(item.get("icon", "") or ""),
                    "homepage": str(item.get("homepage", "") or ""),
                    "tags": [str(t) for t in (item.get("tags") or [])],
                    "requirements": [
                        str(r) for r in (item.get("requirements") or [])
                    ],
                    "updated_at": str(item.get("updated_at", "")),
                }
            )

    def get(self, name: str) -> dict | None:
        for p in self.plugins:
            if p["name"] == name:
                return p
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "plugins": self.plugins}


def _semver_key(version: str) -> tuple[int, ...]:
    """将版本号转为可比较的整数元组（1.2.3 -> (1,2,3)）

    非数字段忽略；仅用于"是否可更新"判断，不做完整 semver 校验。
    """
    parts = []
    for seg in str(version).split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest: str, current: str) -> bool:
    """latest 是否严格大于 current（都按 semver 主.次.补丁比较）"""
    return _semver_key(latest) > _semver_key(current)


class MarketClient:
    """市场索引客户端：拉取 + TTL 缓存 + 磁盘回退"""

    def __init__(
        self,
        url: str = DEFAULT_MARKET_URL,
        *,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    ):
        self.url = url
        self.refresh_interval = refresh_interval
        self._cache: MarketIndex | None = None
        self._fetched_at: float = 0.0  # monotonic，用于 TTL 判断
        self._fetched_wall: float = 0.0  # 墙钟时间，用于展示「索引更新于」

    def _cache_dir(self) -> Path:
        return data_root() / "market"

    def _cache_file(self) -> Path:
        return self._cache_dir() / "index.json"

    def _cache_meta(self) -> Path:
        return self._cache_dir() / "index.meta.json"

    async def get_index(self, *, force: bool = False) -> MarketIndex:
        """获取索引：内存缓存 TTL 内直接返回；否则拉取并刷新缓存

        force=True 强制重新拉取（WebUI「刷新市场」按钮）。
        """
        now = time.monotonic()
        if (
            not force
            and self._cache is not None
            and now - self._fetched_at < self.refresh_interval
        ):
            return self._cache

        try:
            index = await self._fetch_remote()
            self._save_cache(index)
            self._cache = index
            self._fetched_at = now
            self._fetched_wall = time.time()
            logger.info(f"插件市场索引已更新: {index.name} ({len(index.plugins)} 个插件)")
            return index
        except Exception as e:
            logger.warning(f"拉取插件市场索引失败: {e}")
            cached = self._load_cache()
            if cached is not None:
                logger.info("使用本地缓存的插件市场索引")
                self._cache = cached
                self._fetched_at = now
                return cached
            raise MarketError(f"插件市场索引拉取失败且无本地缓存: {e}") from e

    async def _fetch_remote(self) -> MarketIndex:
        """拉取远端索引（git 仓库或 HTTP raw）

        优先尝试 HTTP（快）；返回非 JSON（如 AtomGit 匿名 raw 被拦为 HTML）
        或 git 仓库地址时回退 git clone。
        """
        url = self.url.strip()
        if url.endswith(".git") or "git@" in url:
            return await self._fetch_via_git(url)
        try:
            index = await self._fetch_via_http(url)
            if index.plugins:
                return index
        except Exception as e:
            logger.debug(f"HTTP 拉取市场索引失败，尝试 git: {e}")
        return await self._fetch_via_git(url)

    async def _fetch_via_http(self, url: str) -> MarketIndex:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Qingci-Bot-CE"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return MarketIndex(data)

    async def _fetch_via_git(self, url: str) -> MarketIndex:
        tmp = tempfile.mkdtemp(prefix="qb-market-")
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", url, str(tmp),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                raise MarketError(f"git 克隆市场索引失败: {stdout.decode(errors='replace')[-500:]}")
            index_file = Path(tmp) / "index.json"
            if not index_file.is_file():
                raise MarketError(f"市场仓库中缺少 index.json: {url}")
            return MarketIndex(json.loads(index_file.read_text(encoding="utf-8")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- 磁盘缓存 ----

    def _save_cache(self, index: MarketIndex) -> None:
        try:
            self._cache_dir().mkdir(parents=True, exist_ok=True)
            self._cache_file().write_text(
                json.dumps(index.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._cache_meta().write_text(
                json.dumps({"fetched_at": time.time()}), encoding="utf-8"
            )
        except OSError as e:
            logger.warning(f"写入市场索引缓存失败: {e}")

    def _load_cache(self) -> MarketIndex | None:
        try:
            if not self._cache_file().is_file():
                return None
            data = json.loads(self._cache_file().read_text(encoding="utf-8"))
            return MarketIndex(data)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"读取市场索引缓存失败: {e}")
            return None

    def clear_cache(self) -> None:
        """清空内存缓存（下次访问强制拉取）"""
        self._cache = None
        self._fetched_at = 0.0

    def fetched_at_epoch(self) -> float:
        """最近一次成功拉取索引的时间（epoch 秒）

        优先内存墙钟（本次运行），否则读磁盘缓存 meta（上次运行落盘）。
        从未成功拉取过返回 0.0。
        """
        if self._fetched_wall > 0:
            return self._fetched_wall
        try:
            meta = json.loads(self._cache_meta().read_text(encoding="utf-8"))
            return float(meta.get("fetched_at", 0.0) or 0.0)
        except (OSError, json.JSONDecodeError, ValueError):
            return 0.0


class MarketManager:
    """插件市场编排：合并状态 + 安装/更新"""

    def __init__(
        self,
        client: MarketClient | None = None,
        *,
        url: str = DEFAULT_MARKET_URL,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    ):
        self.client = client or MarketClient(url=url, refresh_interval=refresh_interval)

    async def list_market(self, bot) -> list[dict]:
        """市场列表：合并已安装/未安装/可更新状态

        每个条目额外字段：
        - installed: bool    是否已安装（插件已加载或目录存在）
        - installed_version: str 已安装版本（空串=未安装）
        - update_available: bool 索引版本是否更新
        """
        index = await self.client.get_index()
        installed = self._collect_installed(bot)
        result = []
        for item in index.plugins:
            entry = dict(item)
            cur = installed.get(item["name"], "")
            entry["installed"] = bool(cur)
            entry["installed_version"] = cur
            entry["update_available"] = bool(cur) and is_newer(item["version"], cur)
            result.append(entry)
        return result

    async def market_info(self) -> dict:
        """市场元信息：名称 + 插件数 + 最近索引更新时间"""
        index = await self.client.get_index()
        return {
            "name": index.name,
            "plugin_count": len(index.plugins),
            "fetched_at": self.client.fetched_at_epoch(),
        }

    def _collect_installed(self, bot) -> dict[str, str]:
        """收集已安装插件版本：已加载插件优先，其次插件目录 plugin.json/元数据"""
        installed: dict[str, str] = {}
        for _name, plugin in bot.plugin_manager.plugins.items():
            installed[plugin.name] = plugin.version or "0.0.0"
        # 扫描外部插件目录补充未加载的（目录型）
        from ..paths import plugins_dir

        for child in plugins_dir().iterdir() if plugins_dir().is_dir() else []:
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if child.name in installed:
                continue
            meta = self._load_plugin_json(child)
            if meta:
                installed[child.name] = str(meta.get("version") or "0.0.0")
            else:
                installed[child.name] = "0.0.0"
        return installed

    @staticmethod
    def _load_plugin_json(directory: Path) -> dict | None:
        import json as _json

        json_path = directory / "plugin.json"
        if not json_path.is_file():
            return None
        try:
            data = _json.loads(json_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, _json.JSONDecodeError):
            return None

    async def install(self, bot, name: str) -> bool:
        """安装市场插件（新装或覆盖重装）

        复用 PluginManager.install（git 克隆/HTTP 归档 + 依赖隔离 + 加载）。
        若插件已加载，先卸载再安装（保证代码与实例一致）。
        """
        index = await self.client.get_index()
        item = index.get(name)
        if item is None:
            raise MarketError(f"市场索引中不存在插件: {name}")
        manager = bot.plugin_manager
        if manager.get(name) is not None:
            await manager.unload(name)
        ok = await manager.install(bot, item["source"], name=name)
        if not ok:
            raise MarketError(f"插件 {name} 安装失败，详见服务端日志")
        return True

    async def update(self, bot, name: str) -> bool:
        """更新插件：重新安装（install 内部已处理覆盖重载）"""
        return await self.install(bot, name)

    async def refresh(self) -> MarketIndex:
        """强制刷新索引（绕过 TTL）"""
        return await self.client.get_index(force=True)
