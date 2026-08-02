"""数据库模块 - SQLite 异步操作"""

import asyncio

import aiosqlite
from pathlib import Path
from typing import Optional

DB_PATH = Path("data/qingci-bot.db")


class Database:
    """SQLite 数据库管理器"""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("数据库未初始化，请先调用 connect()")
        return self._conn

    async def connect(self):
        """连接数据库并建表"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()

    async def close(self):
        if self._conn:
            try:
                await asyncio.wait_for(self._conn.close(), timeout=2)
            except Exception:
                pass
            self._conn = None

    async def _create_tables(self):
        """创建数据库表"""
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                group_id INTEGER,
                content TEXT NOT NULL,
                message_type TEXT NOT NULL DEFAULT 'group',
                role TEXT NOT NULL DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id);
            CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

            CREATE TABLE IF NOT EXISTS plugin_configs (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await self._conn.commit()

    # ============ 消息记录 ============

    async def save_message(
        self,
        message_id: str,
        user_id: int,
        content: str,
        message_type: str = "group",
        group_id: Optional[int] = None,
        role: str = "user",
    ):
        await self.conn.execute(
            "INSERT INTO messages (message_id, user_id, group_id, content, message_type, role) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, user_id, group_id, content, message_type, role),
        )
        await self.conn.commit()

    async def get_history(
        self,
        user_id: int,
        group_id: Optional[int] = None,
        limit: int = 20,
    ) -> list[dict]:
        """获取对话历史"""
        if group_id:
            cursor = await self._conn.execute(
                "SELECT user_id, content, role, created_at FROM messages "
                "WHERE group_id = ? AND user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (group_id, user_id, limit),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT user_id, content, role, created_at FROM messages "
                "WHERE user_id = ? AND group_id IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]

    async def search_messages(
        self,
        keyword: str = "",
        user_id: Optional[int] = None,
        group_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """搜索消息记录"""
        conditions = []
        params = []
        if keyword:
            conditions.append("content LIKE ?")
            params.append(f"%{keyword}%")
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if group_id:
            conditions.append("group_id = ?")
            params.append(group_id)

        where = " AND ".join(conditions) if conditions else "1=1"
        cursor = await self._conn.execute(
            f"SELECT * FROM messages WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_message_count(self) -> int:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM messages")
        row = await cursor.fetchone()
        return row[0] if row else 0

    # ============ 插件配置 ============

    async def get_plugin_config(self, key: str) -> Optional[str]:
        cursor = await self._conn.execute(
            "SELECT value FROM plugin_configs WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row["value"] if row else None

    async def set_plugin_config(self, key: str, value: str):
        await self._conn.execute(
            "INSERT OR REPLACE INTO plugin_configs (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value),
        )
        await self._conn.commit()