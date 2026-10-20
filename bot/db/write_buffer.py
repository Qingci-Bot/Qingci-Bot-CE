"""消息写入缓冲 — 把高频单条落库合并为批量提交（write-behind）

背景
----
分发主链路对每条入站消息调用一次 ``save_messages_batch([单条])``，等价于
「每条消息一次 SQLite 事务提交」。SQLite 写锁串行化，提交延迟会直接叠加到
消息处理延迟上；消息洪峰时表现为整体延迟抖升。

方案
----
入队零等待，后台任务按「条数阈值 或 时间窗口」把队列合并为一次批量写入，
把 N 次提交降为 1 次。落库语义由「同步写」放宽为「最终写」：消息日志页
依赖 WS 实时广播即时刷新，不读这条路径的落库结果，故对用户无感知。

降级策略（任何异常都不得影响消息处理）
----
- 队列超上限：丢弃新记录并计数告警（保内存，绝不阻塞事件循环）
- 批量写失败：整批丢弃并记录异常（不做无限重试，避免拖垮进程）
- 关闭：先让后台任务自然退出，再对残留记录做一次同步 flush（带超时）
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

logger = logging.getLogger("qingci-bot.db.buffer")

# 时间窗口（秒）：队列未达阈值时，最长等待这么久也会落库一次
DEFAULT_FLUSH_INTERVAL = 0.2
# 单批条数：达到即立即触发写入（同时决定单次事务的规模）
DEFAULT_MAX_BATCH = 50
# 队列上限：超出即丢弃新记录，防止异常场景下内存无界增长
DEFAULT_MAX_QUEUE = 5000


class MessageWriteBuffer:
    """消息写入缓冲区（单消费者，写入顺序与入队顺序一致）"""

    def __init__(
        self,
        writer: Any,
        *,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        max_batch: int = DEFAULT_MAX_BATCH,
        max_queue: int = DEFAULT_MAX_QUEUE,
    ):
        # writer 需提供 async save_messages_batch(records: list[dict])
        self._writer = writer
        self._flush_interval = max(0.01, float(flush_interval))
        self._max_batch = max(1, int(max_batch))
        self._max_queue = max(self._max_batch, int(max_queue))

        self._queue: deque[dict] = deque()
        self._event: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False

        self._dropped = 0
        self._flushed = 0

    # ---- 观测 ----

    @property
    def pending(self) -> int:
        """当前排队待写入条数"""
        return len(self._queue)

    @property
    def dropped(self) -> int:
        """累计因队列溢出丢弃的条数"""
        return self._dropped

    @property
    def flushed(self) -> int:
        """累计成功落库条数"""
        return self._flushed

    # ---- 写入 ----

    def put(self, record: dict) -> bool:
        """入队一条消息记录（同步、非阻塞），返回是否入队成功

        事件循环外调用（无运行中的 loop，无法调度后台任务）时返回 False，
        由调用方自行决定回退策略。
        """
        if self._closed:
            return False
        if len(self._queue) >= self._max_queue:
            self._dropped += 1
            # 只在首次与每千条时告警，避免洪峰把日志刷爆
            if self._dropped == 1 or self._dropped % 1000 == 0:
                logger.warning(
                    f"消息写入队列已满（上限 {self._max_queue}），累计丢弃 {self._dropped} 条"
                )
            return False

        self._queue.append(record)
        try:
            self._ensure_started()
        except RuntimeError:
            # 无运行中的事件循环：回滚入队，交由调用方降级处理
            self._queue.pop()
            return False

        if len(self._queue) >= self._max_batch and self._event is not None:
            self._event.set()
        return True

    def _ensure_started(self) -> None:
        """惰性创建事件与后台任务（首次 put 时启动，未使用则零开销）

        先取运行中的事件循环再创建协程：无循环时 get_running_loop 直接抛
        RuntimeError，避免「协程对象已创建但 create_task 失败」导致
        "coroutine was never awaited" 告警。
        """
        if self._event is None:
            self._event = asyncio.Event()
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._task is None or self._task.done():
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run(), name="message-write-buffer")

    async def _run(self) -> None:
        """后台消费循环：事件触发或时间窗口到期即落库"""
        assert self._event is not None
        while not (self._closed and not self._queue):
            try:
                await asyncio.wait_for(self._event.wait(), timeout=self._flush_interval)
            except asyncio.TimeoutError:
                pass  # 窗口到期：按当前队列内容落库
            self._event.clear()
            await self._drain()

    async def _drain(self) -> None:
        """把队列按批写入（单批失败不影响后续批次）"""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            while self._queue:
                take = min(self._max_batch, len(self._queue))
                batch = [self._queue.popleft() for _ in range(take)]
                try:
                    await self._writer.save_messages_batch(batch)
                except asyncio.CancelledError:
                    # 停机取消：本批已出队，记录条数便于事后核对
                    logger.warning(f"消息写入被取消，本批 {len(batch)} 条未落库")
                    raise
                except Exception:
                    logger.exception(f"消息批量写入失败，丢弃本批 {len(batch)} 条")
                else:
                    self._flushed += len(batch)

    async def flush(self) -> None:
        """立即写出当前队列（测试、停机、一致性校验用）"""
        await self._drain()

    async def close(self, timeout: float = 5.0) -> None:
        """停止后台任务并落库残留记录（幂等，异常不外抛）"""
        self._closed = True
        if self._event is not None:
            # 唤醒可能正在等待窗口到期的消费循环
            self._event.set()

        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                # shield：wait_for 超时只取消外层等待，不打断正在进行的写入
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                task.cancel()
                logger.warning("消息写入缓冲任务退出超时，已取消")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("消息写入缓冲任务异常退出")

        if self._queue:
            try:
                await asyncio.wait_for(self._drain(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"消息写入缓冲关闭时仍有 {len(self._queue)} 条未落库")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("消息写入缓冲关闭时 flush 失败")
