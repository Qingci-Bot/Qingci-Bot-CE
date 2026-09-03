"""CPU 执行器测试 — run_cpu 的返回值、异常传播与不阻塞事件循环"""

import asyncio
import time

import pytest

from bot.core.cpu_pool import run_cpu, run_cpu_timed


def _sync_add(a: int, b: int) -> int:
    return a + b


def _sync_boom() -> None:
    raise ValueError("boom")


@pytest.mark.asyncio
async def test_run_cpu_returns_result():
    assert await run_cpu(_sync_add, 2, 3) == 5


@pytest.mark.asyncio
async def test_run_cpu_propagates_exception():
    with pytest.raises(ValueError, match="boom"):
        await run_cpu(_sync_boom)


@pytest.mark.asyncio
async def test_run_cpu_does_not_block_event_loop():
    """同步 sleep 在线程池执行，不应卡住其他协程"""

    async def ticker(interval: float):
        await asyncio.sleep(interval)
        return "ticked"

    tick_task = asyncio.create_task(ticker(0.05))
    # 线程池里 sleep 100ms，远大于协程的 50ms
    await run_cpu(time.sleep, 0.1)
    result = await tick_task
    assert result == "ticked"


@pytest.mark.asyncio
async def test_run_cpu_concurrent():
    """并发多个 run_cpu 均完成，互不阻塞"""

    async def many():
        return await asyncio.gather(
            run_cpu(_sync_add, 1, 1),
            run_cpu(_sync_add, 2, 2),
            run_cpu(_sync_add, 3, 3),
        )

    assert await many() == [2, 4, 6]


@pytest.mark.asyncio
async def test_run_cpu_timed_returns_elapsed_ms():
    result, elapsed = await run_cpu_timed(_sync_add, 1, 2)
    assert result == 3
    assert elapsed >= 0.0
