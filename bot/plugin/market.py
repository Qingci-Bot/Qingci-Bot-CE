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
import sys
import tempfile
import time
from pathlib import Path

from ..paths import data_root
from ._proc import NO_WINDOW_FLAG

logger = logging.getLogger("qingci-bot.market")

# 官方插件市场默认索引仓库（Gitee 镜像：GitHub 主仓库的国内自动同步只读镜像，拉取更快更稳）
DEFAULT_MARKET_URL = "https://gitee.com/qingci-bot/Plugin-Market.git"
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
                    "mirror": str(item.get("mirror", "") or ""),
                    "python_requires": str(item.get("python_requires", "") or ""),
                    "icon": str(item.get("icon", "") or ""),
                    "homepage": str(item.get("homepage", "") or ""),
                    "tags": [str(t) for t in (item.get("tags") or [])],
                    "requirements": [str(r) for r in (item.get("requirements") or [])],
                    "updated_at": str(item.get("updated_at", "")),
                    # 归档完整性校验和（可选）：HTTP 归档来源下载后校验，
                    # 防传输被篡改/投毒；git 来源自带完整性，忽略该字段
                    "source_sha256": str(item.get("source_sha256", "") or "").lower(),
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
    """将版本号转为可比较的整数元组（v1.2.3 与 1.2.3 均解析为 (1,2,3)）

    非数字段忽略；仅用于"是否可更新"判断，不做完整 semver 校验。
    """
    text = str(version).strip()
    # 去掉常见的 v/V 前缀，避免首段被解析为 0 导致恒判可更新
    if text[:1].lower() == "v":
        text = text[1:]
    parts = []
    for seg in text.split("."):
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


def _python_compatible(python_requires: str) -> bool:
    """当前 Python 版本是否满足索引声明的 python_requires（PEP 440 specifier）

    未声明或声明无法解析时视为兼容（不阻断安装，仅作展示提示）。
    """
    spec = python_requires.strip()
    if not spec:
        return True
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        return SpecifierSet(spec).contains(Version(sys.version.split()[0]))
    except Exception:
        logger.debug(f"python_requires 解析失败，视为兼容: {python_requires!r}")
        return True


class MarketClient:
    """市场索引客户端：拉取 + TTL 缓存 + 磁盘回退 + 备用源回退"""

    def __init__(
        self,
        url: str = DEFAULT_MARKET_URL,
        *,
        mirror_url: str | None = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    ):
        self.url = url
        self.mirror_url = mirror_url
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
        拉取顺序：主源 url → 备用源 mirror_url → 磁盘缓存 → 报错。
        """
        now = time.monotonic()
        if not force and self._cache is not None and now - self._fetched_at < self.refresh_interval:
            return self._cache

        index = None
        for label, src in (("主源", self.url), ("备用源", self.mirror_url)):
            if not src:
                continue
            try:
                index = await self._fetch_remote(src)
                if label == "备用源":
                    logger.info(f"插件市场索引来自备用源: {src}")
                break
            except Exception as e:
                logger.warning(f"插件市场索引拉取失败（{label} {src}）: {e}")
                index = None

        if index is not None:
            self._save_cache(index)
            self._cache = index
            self._fetched_at = now
            self._fetched_wall = time.time()
            logger.info(f"插件市场索引已更新: {index.name} ({len(index.plugins)} 个插件)")
            return index

        cached = self._load_cache()
        if cached is not None:
            logger.info("使用本地缓存的插件市场索引")
            self._cache = cached
            # 不重置 _fetched_at：保留上次成功拉取时间，TTL 到期后仍会尝试联网刷新
            return cached
        raise MarketError("插件市场索引拉取失败且无本地缓存（主源与备用源均不可用）")

    async def _fetch_remote(self, url: str) -> MarketIndex:
        """拉取远端索引（git 仓库或 HTTP raw）

        优先尝试 HTTP（快）；返回非 JSON（如 Gitee 匿名 raw 被拦为 HTML）
        或 git 仓库地址时回退 git clone。
        """
        url = url.strip()
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

        from .ssrf import NoRedirectHandler, is_private_url

        # 与插件归档下载同基线：市场索引同样拦截私网/环回地址
        if is_private_url(url):
            raise MarketError(f"拒绝拉取私网/环回地址的市场索引: {url}")

        def _sync_fetch() -> MarketIndex:
            # 禁止跟随重定向，防止 302 跳转到内网地址
            opener = urllib.request.build_opener(NoRedirectHandler)
            req = urllib.request.Request(url, headers={"User-Agent": "Qingci-Bot-CE"})
            with opener.open(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return MarketIndex(data)

        # 同步 urllib 放入线程池，避免阻塞事件循环（Bot 与 API 共用同一循环）
        return await asyncio.to_thread(_sync_fetch)

    async def _fetch_via_git(self, url: str) -> MarketIndex:
        from .ssrf import is_allowed_git_url

        if not is_allowed_git_url(url):
            raise MarketError(f"不支持的 git 来源（仅允许 http(s)/ssh/git 协议）: {url}")
        tmp = tempfile.mkdtemp(prefix="qb-market-")
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                url,
                str(tmp),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=NO_WINDOW_FLAG,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                raise MarketError(f"git 克隆市场索引超时: {url}") from None
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
            self._cache_meta().write_text(json.dumps({"fetched_at": time.time()}), encoding="utf-8")
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

    def clear_disk_cache(self) -> None:
        """清空磁盘索引缓存（切换市场源后调用，避免加载旧源的落后数据）"""
        for p in (self._cache_file(), self._cache_meta()):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

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
        mirror_url: str | None = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    ):
        self.client = client or MarketClient(
            url=url, mirror_url=mirror_url, refresh_interval=refresh_interval
        )

    async def list_market(self, bot) -> list[dict]:
        """市场列表：合并已安装/未安装/可更新状态

        每个条目额外字段：
        - installed: bool    是否已安装（插件已加载或目录存在）
        - installed_version: str 已安装版本（空串=未安装）
        - update_available: bool 索引版本是否更新
        - compatible: bool   当前 Python 版本是否满足索引 python_requires
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
            entry["compatible"] = _python_compatible(entry.get("python_requires") or "")
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
        安装地址按 source → mirror（备用）顺序尝试，全部失败才报错。
        """
        index = await self.client.get_index()
        item = index.get(name)
        if item is None:
            raise MarketError(f"市场索引中不存在插件: {name}")
        manager = bot.plugin_manager
        if manager.get(name) is not None:
            await manager.unload(name)
        sources = [s for s in (item.get("source"), item.get("mirror")) if s]
        expected_sha256 = str(item.get("source_sha256", "") or "")
        last_err: Exception | None = None
        for src in sources:
            try:
                # source_sha256 校验仅适用于 HTTP 归档；git 来源在
                # _fetch_plugin 内跳过校验（git 自带完整性）
                if await manager.install(bot, src, name=name, expected_sha256=expected_sha256):
                    return True
            except Exception as e:
                # ensure_dependencies 等抛异常时继续尝试备用源，避免 500 中断
                logger.exception(f"插件 {name} 从 {src} 安装异常: {e}")
                last_err = e
            logger.warning(f"插件 {name} 从 {src} 安装失败，尝试下一个地址")
        # 全部失败：尽力回滚——重新加载磁盘上残留的旧版本，避免插件静默消失
        try:
            from ..paths import plugins_dir

            if (plugins_dir() / name).is_dir():
                await manager.load_external(f"plugins.{name}", bot)
        except Exception:
            logger.exception(f"回滚加载插件 {name} 失败")
        raise MarketError(
            f"插件 {name} 安装失败（已尝试 {len(sources)} 个地址，"
            f"最后错误: {last_err}），详见服务端日志"
        )

    async def update(self, bot, name: str) -> bool:
        """更新插件：重新安装（install 内部已处理覆盖重载）"""
        return await self.install(bot, name)

    async def refresh(self) -> MarketIndex:
        """强制刷新索引（绕过 TTL）"""
        return await self.client.get_index(force=True)
