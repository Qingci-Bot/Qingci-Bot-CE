"""消息写入缓冲（write-behind）测试

覆盖：批量合并、阈值触发、队列溢出降级、关闭时 flush、写入异常隔离，
以及 Database.record_message 的端到端落库。
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from bot.db.write_buffer import MessageWriteBuffer


class _FakeWriter:
    """记录每次批量写入内容的替身 writer"""

    def __init__(self, fail: bool = False):
        self.batches: list[list[dict]] = []
        self.fail = fail

    async def save_messages_batch(self, records: list[dict]) -> None:
        if self.fail:
            raise RuntimeError("模拟写入失败")
        self.batches.append(list(records))


def _rec(i: int) -> dict:
    return {"message_id": f"m{i}", "user_id": 1, "content": f"c{i}"}


class TestMessageWriteBuffer:
    async def test_put_then_flush_writes_single_batch(self):
        writer = _FakeWriter()
        buf = MessageWriteBuffer(writer, flush_interval=10.0, max_batch=50)
        try:
            assert buf.put(_rec(1)) is True
            assert buf.put(_rec(2)) is True
            assert buf.pending == 2

            await buf.flush()

            assert buf.pending == 0
            assert buf.flushed == 2
            assert len(writer.batches) == 1
            assert [r["message_id"] for r in writer.batches[0]] == ["m1", "m2"]
        finally:
            await buf.close()

    async def test_batch_threshold_triggers_background_flush(self):
        writer = _FakeWriter()
        buf = MessageWriteBuffer(writer, flush_interval=10.0, max_batch=3)
        try:
            for i in range(3):
                buf.put(_rec(i))
            # 后台任务在事件循环让出后消费
            for _ in range(20):
                await asyncio.sleep(0.01)
                if buf.pending == 0:
                    break

            assert buf.pending == 0
            assert len(writer.batches) == 1
            assert len(writer.batches[0]) == 3
        finally:
            await buf.close()

    async def test_overflow_drops_new_records_without_blocking(self):
        writer = _FakeWriter()
        # max_batch=1 使阈值不会提前消费；不 await 则后台任务无机会运行
        buf = MessageWriteBuffer(writer, flush_interval=10.0, max_batch=1, max_queue=3)
        try:
            accepted = [buf.put(_rec(i)) for i in range(5)]

            assert accepted == [True, True, True, False, False]
            assert buf.dropped == 2
            assert buf.pending == 3
        finally:
            await buf.close()

    async def test_close_flushes_remaining_records(self):
        writer = _FakeWriter()
        buf = MessageWriteBuffer(writer, flush_interval=10.0, max_batch=50)
        buf.put(_rec(1))
        buf.put(_rec(2))

        await buf.close()

        assert buf.pending == 0
        assert sum(len(b) for b in writer.batches) == 2

    async def test_close_is_idempotent(self):
        writer = _FakeWriter()
        buf = MessageWriteBuffer(writer, flush_interval=10.0)
        buf.put(_rec(1))
        await buf.close()
        await buf.close()  # 二次调用不得抛错

        assert buf.pending == 0

    async def test_put_after_close_rejected(self):
        buf = MessageWriteBuffer(_FakeWriter(), flush_interval=10.0)
        await buf.close()

        assert buf.put(_rec(1)) is False
        assert buf.pending == 0

    async def test_writer_failure_is_isolated(self):
        writer = _FakeWriter(fail=True)
        buf = MessageWriteBuffer(writer, flush_interval=10.0, max_batch=2)
        try:
            buf.put(_rec(1))
            buf.put(_rec(2))

            # 写入失败只记录日志，不得向上抛异常（否则会打断分发链路）
            await buf.flush()

            assert buf.pending == 0
            assert buf.flushed == 0
        finally:
            await buf.close()

    async def test_no_running_loop_returns_false(self):
        """事件循环外调用 put：无法调度后台任务，应回退为不入队"""
        buf = MessageWriteBuffer(_FakeWriter(), flush_interval=10.0)
        result: list[bool] = []

        # 独立线程内无运行中的事件循环，模拟同步上下文调用
        import threading

        def _worker():
            result.append(buf.put(_rec(1)))

        t = threading.Thread(target=_worker)
        t.start()
        t.join()

        assert result == [False]
        assert buf.pending == 0


class TestDatabaseMessageBuffer:
    """Database 集成：record_message 入队，flush_messages 落库"""

    @pytest.fixture
    async def temp_db_dir(self):
        tmp = tempfile.mkdtemp()
        yield Path(tmp)
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.fixture
    async def db(self, temp_db_dir, monkeypatch):
        import bot.db.engine as _engine

        _engine._engine = None
        _engine._session_factory = None
        monkeypatch.setattr(_engine, "data_root", lambda: temp_db_dir)
        await _engine.init_db()

        from bot.db.database import Database

        d = Database()
        yield d

        # close() 会先停掉缓冲后台任务再释放引擎，避免残留任务与已释放引擎竞争
        await d.close()
        _engine._engine = None
        _engine._session_factory = None

    async def test_record_then_flush_persists(self, db):
        assert db.record_message(
            {
                "message_id": "buf-1",
                "user_id": 10001,
                "group_id": 50001,
                "content": "buffered",
                "message_type": "group",
                "role": "user",
                "platform": "onebot",
            }
        )
        # 入队阶段尚未落库
        assert db.pending_messages == 1

        await db.flush_messages()

        assert db.pending_messages == 0
        history = await db.get_history(user_id=10001, group_id=50001, limit=10)
        assert [m["content"] for m in history] == ["buffered"]
