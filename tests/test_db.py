"""数据库操作测试（使用临时 SQLite 文件）"""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_db_dir():
    """创建临时数据库目录"""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    # 清理
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
async def db(temp_db_dir, monkeypatch):
    """设置测试数据库（每个测试独立）"""
    # 重置引擎全局状态
    import bot.db.engine as _engine

    _engine._engine = None
    _engine._session_factory = None

    # monkeypatch DB_PATH（模块级变量，仅在首次 import 时计算）
    db_path = temp_db_dir / "data" / "qingci-bot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_engine, "DB_PATH", db_path)

    await _engine.init_db()
    from bot.db.database import Database

    d = Database()
    yield d

    # 清理
    await _engine.dispose_engine()
    _engine._engine = None
    _engine._session_factory = None


@pytest.mark.asyncio
class TestMessage:
    """消息记录测试"""

    async def test_save_and_get_message(self, db):
        db = db
        await db.save_message(
            message_id="test-001",
            user_id=10001,
            content="Hello World",
            message_type="group",
            group_id=50001,
            role="user",
        )
        history = await db.get_history(user_id=10001, group_id=50001, limit=10)
        assert len(history) > 0
        assert history[0]["content"] == "Hello World"
        assert history[0]["user_id"] == 10001

    async def test_save_message_duplicate(self, db):
        db = db
        await db.save_message("test-dup", 10001, "Test", role="user")
        # 第二次保存应不抛异常（幂等）
        await db.save_message("test-dup", 10001, "Test", role="user")

    async def test_get_message_count(self, db):
        db = db
        await db.save_message("msg-1", 10001, "A", role="user")
        await db.save_message("msg-2", 10001, "B", role="assistant")
        count = await db.get_message_count()
        assert count == 2

    async def test_search_messages(self, db):
        db = db
        await db.save_message("search-1", 10001, "Hello Python", role="user")
        await db.save_message("search-2", 10001, "Hello World", role="user")
        results = await db.search_messages(keyword="Python", limit=10)
        assert len(results) == 1
        assert results[0]["content"] == "Hello Python"

    async def test_clear_messages(self, db):
        db = db
        await db.save_message("clear-1", 10001, "Test", role="user")
        deleted = await db.clear_messages(user_id=10001)
        assert deleted > 0
        count = await db.get_message_count()
        assert count == 0


@pytest.mark.asyncio
class TestSession:
    """会话持久化测试"""

    async def test_save_and_get_session(self, db):
        db = db
        key = "private:10001"
        await db.save_session(key, "user", "Hello")
        await db.save_session(key, "assistant", "Hi there")
        sessions = await db.get_sessions(key, limit=10)
        assert len(sessions) == 2
        assert sessions[0]["role"] == "user"
        assert sessions[1]["role"] == "assistant"

    async def test_clear_session(self, db):
        db = db
        key = "group:50001:10001"
        await db.save_session(key, "user", "Test")
        await db.clear_sessions(key)
        sessions = await db.get_sessions(key)
        assert len(sessions) == 0

    async def test_delete_last_session(self, db):
        db = db
        key = "private:10002"
        await db.save_session(key, "user", "A")
        await db.save_session(key, "assistant", "B")
        await db.delete_last_session(key, "assistant")
        sessions = await db.get_sessions(key)
        assert len(sessions) == 1
        assert sessions[0]["role"] == "user"

    async def test_trim_sessions(self, db):
        db = db
        key = "private:10003"
        for i in range(10):
            await db.save_session(key, "user" if i % 2 == 0 else "assistant", f"msg{i}")
        await db.trim_sessions(key, keep=4)
        sessions = await db.get_sessions(key)
        assert len(sessions) == 4


@pytest.mark.asyncio
class TestPluginConfig:
    """插件配置测试"""

    async def test_set_and_get(self, db):
        db = db
        await db.set_plugin_config("test.plugin.enabled", "true")
        value = await db.get_plugin_config("test.plugin.enabled")
        assert value == "true"

    async def test_get_nonexistent(self, db):
        db = db
        value = await db.get_plugin_config("nonexistent.key")
        assert value is None

    async def test_upsert(self, db):
        db = db
        await db.set_plugin_config("test.key", "v1")
        await db.set_plugin_config("test.key", "v2")
        value = await db.get_plugin_config("test.key")
        assert value == "v2"
