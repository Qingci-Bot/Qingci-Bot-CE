# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""热重载读写屏障

独立成模块的原因：该锁与插件注册表、调度缓存均无状态耦合，
仅按"当前任务是否持读/写锁"做重入裁决，便于单独测试。
"""

import asyncio


class _ReloadRWLock:
    """热重载读写屏障：事件分发共享读、重载独占写

    防止重载窗口内（importlib.reload 就地替换模块 dict / 注册表替换）
    事件分发读到半新半旧的插件状态。同任务重入安全：
    - handler 内触发 reload（读→写）：acquire_write 返回 False，跳过写锁
    - 写锁持有者内重新读（写→读）：acquire_read 直接放行
    - 嵌套写（reload 内部再走 _register_from_module）：幂等放行
    """

    def __init__(self) -> None:
        self._readers: set[asyncio.Task] = set()
        self._writer: asyncio.Task | None = None
        self._cond = asyncio.Condition()

    def _current_task(self) -> asyncio.Task | None:
        """当前任务；事件循环外调用时返回 None（防御性，不抛错）"""
        return asyncio.current_task()

    async def acquire_read(self) -> None:
        task = self._current_task()
        if task is None:
            return  # 事件循环外调用：无锁语义
        if task is self._writer:
            return  # 写锁持有者内重入读：直接放行
        async with self._cond:
            while self._writer is not None:
                await self._cond.wait()
            self._readers.add(task)

    async def release_read(self) -> None:
        task = self._current_task()
        if task is None:
            return
        if task is self._writer:
            return
        async with self._cond:
            if task in self._readers:
                self._readers.discard(task)
                # 无论是否有写者在等都要 notify：等待中的写者尚未设置
                # _writer，其唤醒依赖读者释放时通知（while 循环防误唤醒）
                if not self._readers:
                    self._cond.notify_all()

    async def acquire_write(self) -> bool:
        """返回 False 表示本任务已持读锁（读→写重入），调用方须跳过写锁"""
        task = self._current_task()
        if task is None:
            return False
        if task in self._readers:
            return False
        async with self._cond:
            while self._writer is not None or self._readers:
                if self._writer is task:
                    return True  # 嵌套写：已由本任务持有，幂等放行
                await self._cond.wait()
            self._writer = task
        return True

    async def release_write(self) -> None:
        task = self._current_task()
        if task is None or self._writer is not task:
            return
        async with self._cond:
            self._writer = None
            self._cond.notify_all()
