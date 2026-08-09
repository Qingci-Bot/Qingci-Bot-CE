"""后台任务统一管理：保存引用防止被 GC 提前回收，完成时记录异常日志"""
import asyncio
import logging
from typing import Coroutine

logger = logging.getLogger("qingci-bot.tasks")

_tasks: set[asyncio.Task] = set()


def spawn_background_task(coro: Coroutine, name: str = "", log_errors: bool = True) -> asyncio.Task:
    """创建后台任务并保存引用，完成后记录异常日志，避免异常静默丢失。

    注意：log_errors=False 用于 AlertHandler 等日志敏感场景——
    告警任务自身的异常若走标准日志通道，会被 AlertHandler 计入错误计数形成反馈环。
    """
    task = asyncio.create_task(coro, name=name or None)
    _tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if t.cancelled() or not log_errors:
            return
        exc = t.exception()
        if exc is not None:
            logger.exception(f"后台任务 {name or t.get_name()} 执行失败", exc_info=exc)

    task.add_done_callback(_on_done)
    return task


async def await_pending_tasks(timeout: float = 3.0) -> None:
    """等待所有后台任务完成（用于停机前 flush 异步 DB 写入等）。

    超时后取消剩余任务，避免阻塞停机。
    """
    if not _tasks:
        return
    pending = set(_tasks)
    done, remaining = await asyncio.wait(pending, timeout=timeout)
    for t in remaining:
        t.cancel()
    if remaining:
        await asyncio.gather(*remaining, return_exceptions=True)
