"""SQLite → PostgreSQL 数据迁移工具

用法：
    python scripts/migrate_sqlite_to_pg.py \
      --pg-url "postgresql+asyncpg://user:password@localhost:5432/qingci_bot"

可选参数：
    --sqlite-path  data/qingci-bot.db  # SQLite 数据库路径（默认 data/qingci-bot.db）
    --dry-run                          # 仅读取 SQLite 数据并打印统计，不写入 PG
    --skip-tables sessions,usage_logs  # 跳过指定表（逗号分隔）

迁移策略：
    - 读取 SQLite 中所有表数据
    - 在 PostgreSQL 中创建表结构（SQLModel.metadata.create_all）
    - 逐表迁移，分批插入以控制内存
    - 自动重置 PostgreSQL 自增序列
    - 迁移前检查 PG 目标表是否为空（防止重复迁移）
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate")

# 迁移表顺序（按依赖关系）
TABLE_ORDER = [
    "messages",
    "sessions",
    "plugin_configs",
    "group_configs",
    "usage_logs",
    "audit_logs",
    "knowledge_items",
]

BATCH_SIZE = 500  # 每批写入行数


def parse_args():
    parser = argparse.ArgumentParser(
        description="SQLite → PostgreSQL 数据迁移工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scripts/migrate_sqlite_to_pg.py --pg-url "postgresql+asyncpg://user:pass@localhost:5432/qingci_bot"
  python scripts/migrate_sqlite_to_pg.py --pg-url "..." --dry-run
  python scripts/migrate_sqlite_to_pg.py --pg-url "..." --skip-tables usage_logs,audit_logs
        """,
    )
    parser.add_argument(
        "--pg-url",
        required=True,
        help="PostgreSQL 连接串，格式: postgresql+asyncpg://user:pass@host:port/db",
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(PROJECT_ROOT / "data" / "qingci-bot.db"),
        help="SQLite 数据库路径（默认: data/qingci-bot.db）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅读取 SQLite 数据并打印统计，不写入 PostgreSQL",
    )
    parser.add_argument(
        "--skip-tables",
        default="",
        help="跳过指定表（逗号分隔），如: sessions,usage_logs",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使 PG 目标表已有数据也继续迁移（追加模式）",
    )
    return parser.parse_args()


async def read_sqlite_tables(sqlite_path: Path) -> dict[str, list[dict]]:
    """读取 SQLite 中所有表数据，返回 {table_name: [row_dict, ...]}"""
    import sqlite3

    if not sqlite_path.exists():
        logger.error(f"SQLite 数据库不存在: {sqlite_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row

    tables = {}
    for table_name in TABLE_ORDER:
        try:
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        except sqlite3.OperationalError:
            logger.debug(f"表 {table_name} 不存在，跳过")
            continue
        tables[table_name] = [dict(row) for row in rows]
        logger.info(f"  SQLite [{table_name}]: {len(rows)} 行")

    conn.close()
    return tables


async def create_pg_tables(pg_url: str):
    """在 PostgreSQL 中创建表结构"""
    # 导入所有模型以注册到 SQLModel.metadata
    import importlib

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel

    importlib.import_module("bot.db.models")

    engine = create_async_engine(pg_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        logger.info("  PostgreSQL 表结构已创建")
    finally:
        await engine.dispose()


async def check_pg_tables_empty(pg_url: str, tables: list[str]) -> list[str]:
    """检查 PG 目标表是否为空，返回非空表列表"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg_url, echo=False)
    non_empty = []
    try:
        async with engine.connect() as conn:
            for table_name in tables:
                result = await conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                count = result.scalar()
                if count > 0:
                    non_empty.append(table_name)
                    logger.warning(f"  PG [{table_name}] 已有 {count} 行数据")
    finally:
        await engine.dispose()
    return non_empty


async def migrate_table(
    pg_url: str,
    table_name: str,
    rows: list[dict],
    dry_run: bool = False,
) -> int:
    """迁移单表数据到 PostgreSQL，返回写入行数"""
    if not rows:
        return 0

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    if dry_run:
        sample = rows[0]
        columns = list(sample.keys())
        logger.info(f"  [DRY-RUN] {table_name}: {len(rows)} 行, 列: {columns}")
        return 0

    engine = create_async_engine(pg_url, echo=False)
    written = 0
    try:
        columns = list(rows[0].keys())
        col_names = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)

        # 分批写入
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            async with engine.begin() as conn:
                for row in batch:
                    # 处理 datetime 字符串 → Python datetime
                    cleaned = {}
                    for k, v in row.items():
                        if isinstance(v, str) and _looks_like_iso_datetime(v):
                            cleaned[k] = _parse_iso_datetime(v)
                        else:
                            cleaned[k] = v
                    await conn.execute(
                        text(f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"),
                        cleaned,
                    )
            written += len(batch)
            logger.info(f"  [{table_name}] {written}/{len(rows)}")
    finally:
        await engine.dispose()

    return written


def _looks_like_iso_datetime(value: str) -> bool:
    """判断字符串是否像 ISO 格式时间戳"""
    return len(value) >= 19 and "T" in value and value[4] == "-"


def _parse_iso_datetime(value: str) -> datetime:
    """解析 ISO 格式时间戳字符串为 timezone-aware datetime"""
    try:
        # 处理 SQLite 存储的 ISO 格式
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


async def reset_pg_sequences(pg_url: str, tables: list[str]):
    """重置 PostgreSQL 自增序列（确保与数据一致）"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg_url, echo=False)
    try:
        async with engine.begin() as conn:
            for table_name in tables:
                # 仅对含自增 id 的表重置序列
                try:
                    await conn.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
                        )
                    )
                    logger.info(f"  序列 [{table_name}_id_seq] 已重置")
                except Exception:
                    # 表可能没有自增 id 列（如 plugin_configs, group_configs）
                    pass
    finally:
        await engine.dispose()


async def main():
    args = parse_args()

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.is_absolute():
        sqlite_path = PROJECT_ROOT / sqlite_path

    skip_tables = {t.strip() for t in args.skip_tables.split(",") if t.strip()}

    print("=" * 60)
    print("  Qingci-Bot CE SQLite → PostgreSQL 迁移工具")
    print("=" * 60)
    print(f"  SQLite:    {sqlite_path}")
    print(f"  PG URL:    {args.pg_url}")
    if args.dry_run:
        print("  模式:      DRY-RUN（仅统计，不写入）")
    if skip_tables:
        print(f"  跳过表:    {', '.join(sorted(skip_tables))}")
    print("=" * 60)
    print()

    # 1. 读取 SQLite 数据
    logger.info("步骤 1/4: 读取 SQLite 数据...")
    all_tables = await read_sqlite_tables(sqlite_path)
    if not all_tables:
        logger.error("SQLite 中没有任何数据表，退出")
        return

    total_rows = sum(len(v) for v in all_tables.values())
    logger.info(f"共读取 {len(all_tables)} 个表, {total_rows} 行数据\n")

    if args.dry_run:
        logger.info("DRY-RUN 完成，未写入任何数据")
        return

    # 2. 检查目标表
    logger.info("步骤 2/4: 检查 PostgreSQL 目标表...")
    tables_to_migrate = [t for t in TABLE_ORDER if t in all_tables and t not in skip_tables]
    non_empty = await check_pg_tables_empty(args.pg_url, tables_to_migrate)
    if non_empty and not args.force:
        logger.error(f"以下 PG 表已有数据: {non_empty}。使用 --force 追加迁移，或清空目标表后重试")
        sys.exit(1)

    # 3. 创建表结构
    logger.info("步骤 3/4: 创建 PostgreSQL 表结构...")
    await create_pg_tables(args.pg_url)

    # 4. 迁移数据
    logger.info("\n步骤 4/4: 迁移数据...")
    migrated = {}
    for table_name in tables_to_migrate:
        rows = all_tables[table_name]
        if not rows:
            continue
        migrated[table_name] = await migrate_table(args.pg_url, table_name, rows)

    # 5. 重置序列
    await reset_pg_sequences(args.pg_url, list(migrated.keys()))

    # 总结
    print()
    print("=" * 60)
    print("  迁移完成!")
    print("=" * 60)
    for table_name, count in migrated.items():
        print(f"  {table_name:20s} {count:>8} 行")
    total = sum(migrated.values())
    print(f"  {'─' * 30}")
    print(f"  {'合计':20s} {total:>8} 行")
    print("=" * 60)
    print()
    print("下一步:")
    print("  1. 修改 bot/db/engine.py 中的连接串为 PostgreSQL")
    print("  2. 移除 SQLite 专用 PRAGMA 设置")
    print("  3. 重启 Qingci-Bot CE 验证数据完整性")


if __name__ == "__main__":
    asyncio.run(main())
