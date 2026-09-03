"""CPU 执行器 — 把同步 CPU 计算移出事件循环的防御性基础设施

单进程 asyncio 事件循环内，任何同步重计算（图片解码/缩放、哈希、正则
重解析、昂贵解析、同步库调用）都会卡住整套 Bot（消息、API、其他插件）。
本模块提供统一出口，供 async handler 把这类纯计算委托给线程池执行。

    # 约定用法（在 async handler / 插件中）
    result = await run_cpu(sync_fn, arg1, arg2)

使用约定：
- 用：纯同步 CPU 计算 —— 图片解码/缩放、哈希、正则在大文本上的重解析、
  复杂解析、必须同步调用的 CPU 密集型库。
- 不用：已经是异步 IO 的调用（litellm / playwright / 网络请求 / DB 异步
  会话）。它们本就事件循环友好，再包一层反而多一次线程切换。

实现基于 asyncio.to_thread（事件循环默认 executor，默认
max_workers = min(32, os.cpu_count() + 4)），对 Bot 场景充足，无需自建
独立进程池。默认零侵入：正常路径不额外计时。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any


async def run_cpu(fn, *args: Any, **kwargs: Any) -> Any:
    """在默认线程池中执行同步函数 fn，返回其结果

    同步函数内的异常会照常向上传播到调用点（await 处可捕获）。
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


async def run_cpu_timed(fn, *args: Any, **kwargs: Any) -> tuple[Any, float]:
    """同 run_cpu，但额外返回执行耗时（毫秒），便于调用方自行记录慢计算"""
    started = time.perf_counter()
    result = await asyncio.to_thread(fn, *args, **kwargs)
    return result, (time.perf_counter() - started) * 1000.0
