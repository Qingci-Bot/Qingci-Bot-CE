"""插件开发期自动热重载（文件监听）

定时轮询外部插件目录（plugins/）的 .py 文件修改时间，
检测到变更后自动重载对应插件。用于开发期提升迭代效率，
生产环境建议通过配置关闭。
"""

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger("qingci-bot.plugin.watcher")


class PluginWatcher:
    """监听插件目录文件变更并自动重载插件"""

    def __init__(self, manager, bot, directory: Path, interval: float = 2.0):
        self._manager = manager
        self._bot = bot
        self._directory = directory
        self._interval = max(0.5, float(interval))
        self._task: asyncio.Task | None = None
        self._snapshot: dict[str, int] = {}
        self._running = False

    def _snapshot_dir(self) -> dict[str, int]:
        """扫描插件目录，返回 {文件绝对路径: mtime_ns}"""
        snap: dict[str, int] = {}
        if not self._directory.is_dir():
            return snap
        for py in self._directory.rglob("*.py"):
            try:
                snap[str(py)] = py.stat().st_mtime_ns
            except OSError:
                pass
        return snap

    def _plugin_name_from_path(self, path: str) -> str | None:
        """将插件文件路径映射为插件名（用于 reload）"""
        try:
            rel = os.path.relpath(str(path), str(self._directory))
        except ValueError:
            return None
        parts = rel.replace("\\", "/").split("/")
        # 目录型插件：plugins/<name>/__init__.py
        if len(parts) >= 2 and parts[-1] == "__init__.py":
            return parts[0]
        # 文件型插件：plugins/<name>.py
        if len(parts) == 1 and parts[0].endswith(".py"):
            return os.path.splitext(parts[0])[0]
        return None

    async def start(self) -> None:
        """启动监听任务"""
        if self._running:
            return
        self._running = True
        self._snapshot = self._snapshot_dir()
        self._task = asyncio.create_task(self._run(), name="plugin-watcher")
        logger.info(f"插件热重载已启用，监听 {self._directory}（间隔 {self._interval}s）")

    async def _run(self) -> None:
        """轮询循环：检测变更并重载"""
        while self._running:
            await asyncio.sleep(self._interval)
            try:
                current = self._snapshot_dir()
                changed: list[str] = []
                for path, mtime in current.items():
                    if self._snapshot.get(path) != mtime:
                        changed.append(path)
                # 新增文件也触发
                for path in current:
                    if path not in self._snapshot:
                        changed.append(path)
                self._snapshot = current
                for path in changed:
                    await self._reload_plugin(path)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("插件热重载轮询异常")

    async def _reload_plugin(self, path: str) -> None:
        """重载发生变更的插件（若已加载）；加载失败的插件视为修复信号重试加载"""
        name = self._plugin_name_from_path(path)
        if not name:
            return
        if self._manager.get(name) is not None:
            try:
                await self._manager.reload(name, self._bot)
                logger.info(f"热重载插件: {name}（{os.path.basename(path)}）")
            except Exception:
                logger.exception(f"热重载插件 {name} 失败，旧插件保持生效")
            return
        # 插件未加载：若此前加载失败（记录在 _load_errors 中），文件变更说明
        # 开发者已修复代码，应触发重试加载而非一直静默跳过。
        if name in self._manager._load_errors:
            logger.info(
                f"插件 {name} 文件已变更，重试加载（此前失败: {self._manager._load_errors[name]}）"
            )
            try:
                ok = await self._manager.load_external(f"plugins.{name}", self._bot)
                if ok:
                    logger.info(f"插件 {name} 重试加载成功（{os.path.basename(path)}）")
                else:
                    logger.warning(f"插件 {name} 重试加载仍失败，等待下次文件变更")
            except Exception:
                logger.exception(f"插件 {name} 重试加载异常")

    async def stop(self) -> None:
        """停止监听任务"""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("插件热重载已停止")
