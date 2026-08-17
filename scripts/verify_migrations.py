"""验证 Alembic 迁移一致性（CI 冒烟 / 本地自检）

在隔离的临时数据目录上执行：
1. alembic upgrade head —— 空库建表，断言全部业务表创建成功
2. alembic check —— 校验迁移脚本与 SQLModel 模型无漂移（防止模型改动后迁移失效）

用法: python scripts/verify_migrations.py
退出码: 0 通过 / 1 失败
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config

# 确保项目根目录在 sys.path（脚本可能从任意 cwd 调用）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.paths import set_data_root  # noqa: E402

# 业务表（不含 alembic_version / sqlite_sequence）
EXPECTED_TABLES = {
    "messages",
    "sessions",
    "plugin_configs",
    "group_configs",
    "usage_logs",
    "audit_logs",
    "knowledge_items",
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qc-migrate-"))
    set_data_root(tmp)
    print(f"[setup] 隔离数据目录: {tmp}")

    cfg = Config(str(ROOT / "alembic.ini"))

    # 1. 空库迁移到最新
    command.upgrade(cfg, "head")
    print("[1/3] alembic upgrade head 成功")

    # 2. 表完整性断言
    db_file = tmp / "qingci-bot.db"
    if not db_file.is_file():
        print("[FAIL] 迁移后未生成数据库文件")
        return 1
    conn = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    missing = EXPECTED_TABLES - tables
    if missing:
        print(f"[FAIL] 缺少表: {sorted(missing)}")
        return 1
    print(f"[2/3] 表完整性 OK（{len(EXPECTED_TABLES)} 张业务表，head={row[0] if row else '?'}）")

    # 3. 模型漂移检查
    try:
        command.check(cfg)
    except Exception as e:
        print(f"[FAIL] 迁移与模型存在漂移: {e}")
        return 1
    print("[3/3] alembic check 通过：迁移脚本与模型一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
