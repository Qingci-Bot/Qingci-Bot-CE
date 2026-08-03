"""数据库模型定义 - 基于 SQLModel

一个模型类同时承担三种职责：
1. 数据库表结构（table=True）
2. Pydantic 数据校验
3. FastAPI 响应模型
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class Message(SQLModel, table=True):
    """消息记录表"""

    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: str = Field(default="", index=True, unique=True)
    user_id: int = Field(index=True)
    group_id: Optional[int] = Field(default=None, index=True)
    content: str
    message_type: str = Field(default="group")
    role: str = Field(default="user")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )


class SessionHistory(SQLModel, table=True):
    """LLM 会话历史持久化表

    用于跨重启保留对话上下文，session_key 格式：
    - 群聊: group:{group_id}:{user_id}
    - 私聊: private:{user_id}
    """

    __tablename__ = "sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_key: str = Field(index=True)
    role: str  # user / assistant
    content: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )


class PluginConfig(SQLModel, table=True):
    """插件配置 KV 存储"""

    __tablename__ = "plugin_configs"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
