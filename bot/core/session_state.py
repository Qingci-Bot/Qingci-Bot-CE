"""会话状态管理器 — TTL 键值存储

借鉴 NoneBot2 的 session.state 设计，为每个会话（用户/群聊）提供
带过期时间的临时键值存储。适用于多步骤对话、表单填写、临时数据缓存等场景。

使用方式：
    # 在 handler 中通过 ctx.session_state 访问
    ctx.session_state.set("step", "waiting_name", ttl=300)
    name = ctx.session_state.get("step")

    # 在插件中通过 bot.session_state 访问（需 await）
    await self.bot.session_state.set("my_key", value, user_id=123, group_id=456)
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("qingci-bot.session_state")


class SessionState:
    """单个会话的状态存储（TTL 过期）

    同步方法，无需 await。每个会话实例只在单个协程中使用，无并发风险。
    """

    def __init__(self):
        self._data: dict[str, tuple[Any, float]] = {}
        # key -> (value, expire_at)
        self._created_at: float = time.monotonic()

    def get(self, key: str, default: Any = None) -> Any:
        """获取值，过期自动删除"""
        entry = self._data.get(key)
        if entry is None:
            return default
        value, expire_at = entry
        if expire_at > 0 and time.monotonic() > expire_at:
            del self._data[key]
            return default
        return value

    def set(self, key: str, value: Any, ttl: float = 0) -> None:
        """设置值，ttl=0 表示永不过期"""
        expire_at = time.monotonic() + ttl if ttl > 0 else 0
        self._data[key] = (value, expire_at)

    def pop(self, key: str, default: Any = None) -> Any:
        """获取并删除键（类似 dict.pop）"""
        entry = self._data.pop(key, None)
        if entry is None:
            return default
        value, expire_at = entry
        if expire_at > 0 and time.monotonic() > expire_at:
            return default
        return value

    def expire(self, key: str, ttl: float) -> bool:
        """为已有键设置过期时间，不存在时返回 False"""
        entry = self._data.get(key)
        if entry is None:
            return False
        value, _ = entry
        self._data[key] = (value, time.monotonic() + ttl)
        return True

    def delete(self, key: str) -> None:
        """删除键"""
        self._data.pop(key, None)

    def clear(self) -> None:
        """清空所有状态"""
        self._data.clear()

    def keys(self) -> list[str]:
        """返回所有有效键（自动清理过期）"""
        self._cleanup()
        return list(self._data.keys())

    def items(self) -> list[tuple[str, Any]]:
        """返回所有有效键值对（自动清理过期）"""
        self._cleanup()
        return [(k, v) for k, (v, _) in self._data.items()]

    def ttl(self, key: str) -> float | None:
        """获取键的剩余过期时间（秒），不存在返回 None，永不过期返回 -1"""
        entry = self._data.get(key)
        if entry is None:
            return None
        _, expire_at = entry
        if expire_at == 0:
            return -1.0
        remaining = expire_at - time.monotonic()
        if remaining <= 0:
            del self._data[key]
            return None
        return remaining

    def count(self) -> int:
        """获取有效键数量"""
        self._cleanup()
        return len(self._data)

    @property
    def created_at(self) -> float:
        """会话创建时间"""
        return self._created_at

    def _cleanup(self) -> None:
        """清理过期条目"""
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._data.items() if exp > 0 and now > exp]
        for k in expired:
            del self._data[k]


class SessionStateManager:
    """会话状态管理器：管理所有会话的 TTL 状态存储

    会话键格式：
        - private:{user_id}          私聊
        - group:{group_id}:{user_id} 群聊中特定用户
        - group:{group_id}           群聊共享

    所有修改 _states 的方法均使用 asyncio.Lock 保护并发安全。
    """

    def __init__(self):
        self._states: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()
        # 定期清理间隔（秒）
        self._cleanup_interval: float = 300.0
        self._last_cleanup: float = 0.0
        # 最大会话数限制（0 = 不限制）
        self._max_sessions: int = 10000

    # ---- 会话键构建 ----

    @staticmethod
    def _session_key(
        user_id: int = 0,
        group_id: int = 0,
        *,
        message_type: str = "",
        custom_key: str = "",
    ) -> str:
        """构建会话键"""
        if custom_key:
            return custom_key
        if message_type == "group" or group_id:
            gid = group_id or 0
            uid = user_id or 0
            if uid:
                return f"group:{gid}:{uid}"
            return f"group:{gid}"
        if user_id:
            return f"private:{user_id}"
        return "__global__"

    # ---- 状态获取 ----

    async def get_session(
        self,
        user_id: int = 0,
        group_id: int = 0,
        *,
        message_type: str = "",
        custom_key: str = "",
    ) -> SessionState:
        """获取或创建会话状态"""
        key = self._session_key(user_id, group_id, message_type=message_type, custom_key=custom_key)
        async with self._lock:
            # 先清理过期会话，避免误删本次即将创建/返回的会话
            self._maybe_cleanup_locked()
            if key not in self._states:
                if self._max_sessions > 0 and len(self._states) >= self._max_sessions:
                    self._cleanup_expired_locked()
                    if len(self._states) >= self._max_sessions:
                        logger.warning(
                            f"会话数已达上限 {self._max_sessions}，无法创建新会话: {key}"
                        )
                        return SessionState()
                self._states[key] = SessionState()
                logger.debug(f"创建会话: {key}")
            return self._states[key]

    async def remove_session(
        self,
        user_id: int = 0,
        group_id: int = 0,
        *,
        message_type: str = "",
        custom_key: str = "",
    ) -> bool:
        """显式删除会话，返回是否成功删除"""
        key = self._session_key(user_id, group_id, message_type=message_type, custom_key=custom_key)
        async with self._lock:
            if key in self._states:
                del self._states[key]
                logger.debug(f"删除会话: {key}")
                return True
            return False

    # ---- 便捷方法 ----

    async def get(
        self,
        key: str,
        default: Any = None,
        *,
        user_id: int = 0,
        group_id: int = 0,
        message_type: str = "",
    ) -> Any:
        """获取状态值"""
        session = await self.get_session(user_id, group_id, message_type=message_type)
        return session.get(key, default)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: float = 0,
        *,
        user_id: int = 0,
        group_id: int = 0,
        message_type: str = "",
    ) -> None:
        """设置状态值"""
        session = await self.get_session(user_id, group_id, message_type=message_type)
        session.set(key, value, ttl)

    async def delete(
        self,
        key: str,
        *,
        user_id: int = 0,
        group_id: int = 0,
        message_type: str = "",
    ) -> None:
        """删除状态值"""
        session = await self.get_session(user_id, group_id, message_type=message_type)
        session.delete(key)

    async def clear(
        self,
        *,
        user_id: int = 0,
        group_id: int = 0,
        message_type: str = "",
    ) -> None:
        """清空会话状态"""
        session = await self.get_session(user_id, group_id, message_type=message_type)
        session.clear()

    # ---- 全局清理 ----

    async def clear_all(self) -> None:
        """清空所有会话状态"""
        async with self._lock:
            self._states.clear()
            self._last_cleanup = time.monotonic()

    # ---- 统计 ----

    async def stats(self) -> dict[str, Any]:
        """获取会话状态统计信息"""
        async with self._lock:
            total_keys = 0
            for state in self._states.values():
                state._cleanup()
                total_keys += len(state._data)
            return {
                "session_count": len(self._states),
                "total_keys": total_keys,
                "max_sessions": self._max_sessions,
                "last_cleanup": self._last_cleanup,
            }

    # ---- 序列化 ----

    async def serialize(self) -> dict[str, dict[str, Any]]:
        """序列化所有会话状态（用于持久化）

        只序列化永不过期（ttl=0）的键。
        """
        async with self._lock:
            result = {}
            for key, state in self._states.items():
                state._cleanup()
                data = {k: v for k, (v, exp) in state._data.items() if exp == 0}
                if data:
                    result[key] = data
            return result

    async def deserialize(self, data: dict[str, dict[str, Any]]) -> int:
        """从序列化数据恢复会话状态，返回恢复的键数"""
        count = 0
        async with self._lock:
            for session_key, state_data in data.items():
                if not isinstance(state_data, dict):
                    continue
                if session_key not in self._states:
                    self._states[session_key] = SessionState()
                state = self._states[session_key]
                for k, v in state_data.items():
                    state._data[k] = (v, 0)
                    count += 1
        logger.info(f"会话状态恢复完成: {count} 个键")
        return count

    # ---- 内部清理 ----

    def _maybe_cleanup_locked(self) -> None:
        """定期清理过期会话和过期键（需持有锁）

        清理策略：
        1. 移除空会话
        2. 清理活跃会话中的过期键
        """
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        removed_sessions = 0
        empty = []

        for key, state in self._states.items():
            state._cleanup()
            if not state._data:
                empty.append(key)

        for k in empty:
            del self._states[k]
            removed_sessions += 1

        if removed_sessions > 0:
            logger.debug(
                f"会话状态清理: {removed_sessions} 个空会话移除, {len(self._states)} 个活跃会话"
            )

    def _cleanup_expired_locked(self) -> None:
        """强制清理过期会话（需持有锁）"""
        empty = []
        for key, state in self._states.items():
            state._cleanup()
            if not state._data:
                empty.append(key)
        for k in empty:
            del self._states[k]
