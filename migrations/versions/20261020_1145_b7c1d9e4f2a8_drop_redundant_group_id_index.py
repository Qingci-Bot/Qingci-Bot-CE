"""裁剪 messages 表冗余的 group_id 单列索引

messages 表原有 6 个索引，其中 ix_messages_group_id 与复合索引
ix_messages_group_id_created_at 的最左前缀完全重叠——任何按 group_id 的
查询都能由复合索引支撑，单列索引只带来写放大（高频写入表每多一个索引
就多一次 B-Tree 维护）。本迁移移除该冗余索引。

保留的索引与用途：
- ix_messages_group_id_created_at   按群 / 按群+时间范围查询、按群倒序分页
- ix_messages_platform_message_id_created_at  多平台下按消息 ID 定位
- ix_messages_message_id            按消息 ID 单查（非上述复合索引最左前缀）
- ix_messages_user_id               私聊历史、按用户过滤
- ix_messages_created_at            保留期清理、全局时间范围扫描

Revision ID: b7c1d9e4f2a8
Revises: af82430887b7
Create Date: 2026-10-20 11:45:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d9e4f2a8"
down_revision: str | None = "af82430887b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_messages_group_id"))


def downgrade() -> None:
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_messages_group_id"), ["group_id"], unique=False)
