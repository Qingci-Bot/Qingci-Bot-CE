"""定时任务调度器 - APScheduler AsyncIOScheduler 薄封装

设计要点（对齐批次 1 功能 3）：
- add_job 的 job_id 自动加 owner（插件名）前缀，便于按插件批量清理；
- 所有任务函数经异常隔离 wrapper 包装：任务异常只记日志，绝不拖垮调度器；
- start/shutdown 幂等，shutdown(wait=False) 快速返回，配合 stop 的超时保护语义。
"""

import asyncio
import functools
import inspect
import logging
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger("qingci-bot.scheduler")


class BotScheduler:
    """AsyncIOScheduler 薄封装

    与 Bot 共用同一事件循环（AsyncIOScheduler 在 start 时绑定当前 running loop），
    因此必须在 bot.start() 的异步上下文中启动。
    """

    def __init__(self):
        self._scheduler = AsyncIOScheduler()
        self._shutting_down = False  # 防重入：shutdown 生效前避免重复发起关闭

    # ============ 生命周期（幂等） ============

    @property
    def running(self) -> bool:
        """调度器是否正在运行"""
        return self._scheduler.running

    def start(self) -> None:
        """启动调度器（幂等：已运行时直接返回）"""
        if self._scheduler.running:
            return
        self._scheduler.start()
        logger.info("定时任务调度器已启动")

    async def shutdown(self, wait: bool = False) -> None:
        """关闭调度器（幂等：未运行/关闭中时直接返回）

        bot.stop() 有超时保护语义，默认 wait=False 不等待正在执行的任务。
        APScheduler 3.11 的 shutdown 经 call_soon_threadsafe 延迟到下一轮
        事件循环才真正生效，这里主动让出控制权等待状态落地（上限约 2 秒），
        避免退出时残留定时器。
        """
        if self._shutting_down or not self._scheduler.running:
            return
        self._shutting_down = True
        try:
            self._scheduler.shutdown(wait=wait)
        except Exception:
            logger.exception("定时任务调度器关闭异常")
            self._shutting_down = False
            return
        # 等待延迟生效：正常一轮 sleep(0) 即可，上限 40 x 0.05s 兜底
        for _ in range(40):
            if not self._scheduler.running:
                break
            await asyncio.sleep(0.05)
        self._shutting_down = False
        logger.info("定时任务调度器已停止")

    # ============ 任务管理 ============

    def add_job(
        self,
        func: Callable[..., Any],
        trigger: str,
        job_id: str,
        owner: str,
        **trigger_args: Any,
    ):
        """注册定时任务

        Args:
            func: 同步或 async 任务函数（自动包装异常隔离）
            trigger: APScheduler 触发器类型（cron / interval / date）
            job_id: 任务 ID（自动加 owner 前缀，形如 "插件名:job_id"）
            owner: 任务归属方（一般为插件名，用于卸载时批量清理）
            **trigger_args: 触发器参数（如 minute="0" / seconds=60）

        重复注册同一 job_id 时 replace_existing=True 覆盖旧任务，
        避免插件热重载导致任务重复堆积。
        """
        full_id = f"{owner}:{job_id}" if owner else job_id
        wrapped = self._wrap_func(func, full_id)
        if not self._scheduler.running:
            # 未启动时任务进入 APScheduler 的 pending 队列，
            # 手动去重同 id 的旧任务，保证启动前 get_jobs 视图不重复；
            # 新任务的 replace_existing 标记随 pending 项保留，启动时同样生效
            self._scheduler._pending_jobs = [
                item for item in self._scheduler._pending_jobs
                if item[0].id != full_id
            ]
        return self._scheduler.add_job(
            wrapped,
            trigger,
            id=full_id,
            replace_existing=True,
            **trigger_args,
        )

    def remove_jobs_by_owner(self, owner: str) -> None:
        """移除指定 owner（插件名）名下全部任务，卸载插件时兜底调用"""
        prefix = f"{owner}:"

        def _match(job_id: str) -> bool:
            return job_id == owner or job_id.startswith(prefix)

        # 未启动时任务在 pending 队列中，Job.remove() 无法移除，需单独过滤；
        # 已启动时 pending 队列为空，下面再清理 jobstore 中的任务
        if not self._scheduler.running:
            self._scheduler._pending_jobs = [
                item for item in self._scheduler._pending_jobs
                if not _match(item[0].id)
            ]
        for job in list(self._scheduler.get_jobs()):
            if _match(job.id):
                try:
                    job.remove()
                    logger.info(f"已移除定时任务: {job.id}")
                except Exception:
                    logger.exception(f"移除定时任务失败: {job.id}")

    def get_jobs(self) -> list:
        """列出当前全部任务（APScheduler 3.11 未启动时自动返回 pending 任务）"""
        return list(self._scheduler.get_jobs())

    # ============ 异常隔离 wrapper ============

    @staticmethod
    def _wrap_func(func: Callable[..., Any], job_id: str) -> Callable[..., Any]:
        """包装任务函数：捕获任务异常仅记日志，防止异常传播拖垮调度器"""
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    logger.exception(f"定时任务执行异常: {job_id}")
            return _async_wrapper

        @functools.wraps(func)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception:
                logger.exception(f"定时任务执行异常: {job_id}")
        return _sync_wrapper
