"""数据库模型定义 - 基于 SQLModel

一个模型类同时承担三种职责：
1. 数据库表结构（table=True）
2. Pydantic 数据校验
3. FastAPI 响应模型
"""

from datetime import datetime, timezone

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class Message(SQLModel, table=True):
    """消息记录表"""

    __tablename__ = "messages"
    # 复合索引：按群 + 时间范围查询消息；
    # 组合索引：多平台下按 (platform, message_id) 定位消息（方案A迁移：
    # message_id 不再全局唯一，同一 ID 在不同平台可并存）
    __table_args__ = (
        Index("ix_messages_group_id_created_at", "group_id", "created_at"),
        Index(
            "ix_messages_platform_message_id_created_at",
            "platform",
            "message_id",
            "created_at",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    message_id: str = Field(index=True)  # 平台消息 ID（不再唯一）
    user_id: int = Field(index=True)
    group_id: int | None = Field(default=None, index=True)
    platform: str = Field(default="onebot")  # 消息来源平台（onebot / telegram / ...）
    content: str
    message_type: str = Field(default="group")
    role: str = Field(default="user")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class SessionHistory(SQLModel, table=True):
    """LLM 会话历史持久化表

    用于跨重启保留对话上下文，session_key 格式：
    - 群聊: group:{group_id}:{user_id}
    - 私聊: private:{user_id}
    """

    __tablename__ = "sessions"
    # 复合索引：按会话 + 时间范围加载历史
    __table_args__ = (Index("ix_sessions_session_key_created_at", "session_key", "created_at"),)

    id: int | None = Field(default=None, primary_key=True)
    session_key: str = Field(index=True)
    role: str  # user / assistant
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class PluginConfig(SQLModel, table=True):
    """插件配置 KV 存储"""

    __tablename__ = "plugin_configs"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GroupConfig(SQLModel, table=True):
    """群粒度配置表

    trigger_mode 为空表示跟随全局 bot.trigger_mode。
    """

    __tablename__ = "group_configs"

    group_id: int = Field(primary_key=True)
    enabled: bool = Field(default=True)
    trigger_mode: str | None = Field(default=None)  # at / keyword / always，空=跟随全局
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageLog(SQLModel, table=True):
    """LLM 用量统计表

    source 区分调用来源：chat / tool / summary / image。
    """

    __tablename__ = "usage_logs"

    id: int | None = Field(default=None, primary_key=True)
    session_key: str = Field(index=True)
    user_id: int = Field(default=0)
    model: str = Field(default="")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    source: str = Field(default="chat")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class AuditLog(SQLModel, table=True):
    """API 操作审计日志表"""

    __tablename__ = "audit_logs"

    id: int | None = Field(default=None, primary_key=True)
    action: str
    detail: str = Field(default="")
    client_ip: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class KnowledgeItem(SQLModel, table=True):
    """轻量知识库条目表

    预留：未来向量检索使用，当前 RAG 为文件型关键词检索
    （bot/rag/knowledge.py），不读写本表；保留表结构与迁移以免破坏存量库。
    """

    __tablename__ = "knowledge_items"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    content: str
    embedding: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
