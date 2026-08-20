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

    # 将引擎的数据根指向临时目录，使 db_path() 解析到临时库
    monkeypatch.setattr(_engine, "data_root", lambda: temp_db_dir)

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


@pytest.mark.asyncio
class TestSessionScope:
    """session_scope 作用域：成功提交、异常回滚"""

    async def test_commit_on_success(self, db):
        from bot.db.engine import session_scope
        from bot.db.models import SessionHistory

        async with session_scope() as session:
            session.add(SessionHistory(session_key="k", role="user", content="hi"))
        # scope 退出后自动提交，数据可被新会话读到
        sessions = await db.get_sessions("k")
        assert len(sessions) == 1
        assert sessions[0]["content"] == "hi"

    async def test_rollback_on_error(self, db):
        from bot.db.engine import session_scope
        from bot.db.models import SessionHistory

        with pytest.raises(RuntimeError):
            async with session_scope() as session:
                # 事务内新增一条待提交记录
                session.add(SessionHistory(session_key="k", role="user", content="pending"))
                raise RuntimeError("boom")
        # 异常回滚：待提交记录未落库，仅保留此前已提交的记录
        sessions = await db.get_sessions("k")
        assert sessions == []


@pytest.mark.asyncio
class TestDataRetention:
    """数据保留清理（_purge_expired_data）：删除超过保留期的历史记录"""

    async def test_purge_removes_expired_keeps_recent(self, db):
        import datetime

        from bot.core.bot import QingciBot
        from bot.db.engine import session_scope
        from bot.db.models import AuditLog, Message, SessionHistory, UsageLog

        now = datetime.datetime.now(datetime.timezone.utc)
        old = now - datetime.timedelta(days=30)
        recent = now - datetime.timedelta(days=1)

        async with session_scope() as session:
            session.add(
                Message(message_id="old-m", user_id=1, content="old", role="user", created_at=old)
            )
            session.add(
                Message(
                    message_id="new-m", user_id=1, content="new", role="user", created_at=recent
                )
            )
            session.add(
                SessionHistory(session_key="k", role="user", content="old-s", created_at=old)
            )
            session.add(UsageLog(session_key="k", model="gpt", created_at=old))
            session.add(AuditLog(action="test", detail="old-a", created_at=old))

        # retention_days=7：仅删除 30 天前的
        await QingciBot._purge_expired_data(None, 7)

        async with session_scope() as session:
            from sqlalchemy import func, select

            msg_count = (
                await session.execute(select(func.count()).select_from(Message))
            ).scalar_one()
            sess_count = (
                await session.execute(select(func.count()).select_from(SessionHistory))
            ).scalar_one()
            usage_count = (
                await session.execute(select(func.count()).select_from(UsageLog))
            ).scalar_one()
            audit_count = (
                await session.execute(select(func.count()).select_from(AuditLog))
            ).scalar_one()

        assert msg_count == 1  # 旧消息被删，新消息保留
        assert sess_count == 0
        assert usage_count == 0
        assert audit_count == 0
