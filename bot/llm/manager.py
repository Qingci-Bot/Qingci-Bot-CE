"""LLM 管理器 - 多模型管理、会话上下文、Token 裁剪、持久化

特性：
- 基于 LiteLLMAdapter 统一调用 100+ LLM 提供商
- 会话历史双写：内存缓存 + 数据库持久化（可选）
- 按 max_history 条数与 max_context_tokens 双重裁剪
- clear_session 保持同步签名（兼容旧调用方），DB 清除异步触发
"""

import asyncio
import logging
from typing import AsyncIterator, Optional

from ..config import LLMConfig
from ..db.database import Database
from .adapter import LLMAdapter
from .litellm_adapter import LiteLLMAdapter

logger = logging.getLogger("qingci-bot.llm.manager")


class LLMManager:
    """LLM 管理器：适配器管理、会话上下文、Token 裁剪"""

    def __init__(self, config: LLMConfig, db: Optional[Database] = None):
        self._config = config
        self._db = db
        self._adapter: Optional[LLMAdapter] = None
        # 内存会话缓存: key = "group:{group_id}:{user_id}" 或 "private:{user_id}"
        self._sessions: dict[str, list[dict]] = {}
        # 已从 DB 懒加载过的 session key
        self._loaded_sessions: set[str] = set()

    # ============ 适配器管理 ============

    def _create_adapter(self) -> LLMAdapter:
        """根据配置创建 litellm 适配器（统一入口）"""
        return LiteLLMAdapter(
            provider=self._config.provider,
            api_url=self._config.api_url,
            api_key=self._config.api_key,
            model=self._config.model,
        )

    @property
    def adapter(self) -> LLMAdapter:
        if self._adapter is None:
            self._adapter = self._create_adapter()
        return self._adapter

    async def reload(self, config: LLMConfig):
        """重新加载配置并重置适配器（会话缓存保留）"""
        self._config = config
        await self.close()

    async def close(self):
        """关闭适配器，释放资源"""
        if self._adapter:
            try:
                await self._adapter.close()
            except Exception:
                logger.exception("关闭 LLM 适配器异常")
            self._adapter = None

    # ============ 会话管理 ============

    def _session_key(self, message_type: str, group_id: int, user_id: int) -> str:
        if message_type == "private":
            return f"private:{user_id}"
        return f"group:{group_id}:{user_id}"

    async def _ensure_session_loaded(self, key: str):
        """懒加载：首次访问某会话时从 DB 读取历史"""
        if key in self._loaded_sessions:
            return
        self._loaded_sessions.add(key)
        if self._db is None:
            self._sessions.setdefault(key, [])
            return
        try:
            rows = await self._db.get_sessions(key, limit=self._config.max_history * 2)
            self._sessions[key] = [
                {"role": r["role"], "content": r["content"]} for r in rows
            ]
        except Exception:
            logger.exception(f"加载会话历史失败: {key}")
            self._sessions.setdefault(key, [])

    def _trim_history(self, key: str):
        """双重裁剪：max_history 条数 + max_context_tokens"""
        msgs = self._sessions.get(key, [])
        if not msgs:
            return

        # 1. 按条数裁剪（每轮 = user + assistant）
        max_msgs = self._config.max_history * 2
        if len(msgs) > max_msgs:
            msgs = msgs[-max_msgs:]
            self._sessions[key] = msgs

        # 2. 按 token 上限裁剪（保留最近至少 1 轮）
        max_tokens = self._config.max_context_tokens
        if max_tokens <= 0:
            return
        try:
            import litellm
            while len(msgs) > 2 and litellm.token_counter(self._config.model, messages=msgs) > max_tokens:
                msgs.pop(0)
        except Exception:
            # 降级：粗略估算（中文≈1.5 token/字符）
            while len(msgs) > 2 and sum(
                len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                for m in msgs
            ) * 3 // 2 > max_tokens:
                msgs.pop(0)

    def clear_session(self, message_type: str = "", group_id: int = 0, user_id: int = 0):
        """清除会话历史（同步清内存，异步清 DB）

        保持同步签名以兼容 admin.py / log.py 的同步调用。
        """
        if message_type and user_id:
            key = self._session_key(message_type, group_id, user_id)
            self._sessions.pop(key, None)
            self._loaded_sessions.discard(key)
            self._schedule_db_clear(key)
        else:
            self._sessions.clear()
            self._loaded_sessions.clear()
            self._schedule_db_clear(None)

    def _schedule_db_clear(self, key: Optional[str]):
        """异步触发数据库会话清除（fire-and-forget）"""
        if self._db is None:
            return
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self._db.clear_sessions(key))
        except RuntimeError:
            logger.warning("无事件循环，跳过数据库会话清除")

    # ============ 对话调用 ============

    async def chat(
        self,
        message: str,
        message_type: str = "group",
        group_id: int = 0,
        user_id: int = 0,
        user_name: str = "",
        images: Optional[list[str]] = None,
    ) -> str:
        """同步聊天（返回完整回复）"""
        key = self._session_key(message_type, group_id, user_id)
        await self._ensure_session_loaded(key)

        # 追加用户消息
        self._sessions.setdefault(key, []).append({"role": "user", "content": message})
        # 持久化用户消息
        if self._db is not None:
            try:
                await self._db.save_session(key, "user", message)
            except Exception:
                logger.exception("持久化用户消息失败")

        # 裁剪上下文
        self._trim_history(key)

        # 调用 LLM
        try:
            reply = await self.adapter.chat(
                messages=self._sessions[key],
                system_prompt=self._config.system_prompt,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                images=images,
            )
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            # 回滚刚加入的用户消息，保持历史一致性
            if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                self._sessions[key].pop()
            return "抱歉，AI 服务暂时不可用，请稍后再试。"

        # 保存助手回复
        self._sessions[key].append({"role": "assistant", "content": reply})
        if self._db is not None:
            try:
                await self._db.save_session(key, "assistant", reply)
            except Exception:
                logger.exception("持久化助手回复失败")
        return reply

    async def chat_stream(
        self,
        message: str,
        message_type: str = "group",
        group_id: int = 0,
        user_id: int = 0,
    ) -> AsyncIterator[str]:
        """流式聊天（逐段返回增量文本）"""
        key = self._session_key(message_type, group_id, user_id)
        await self._ensure_session_loaded(key)

        self._sessions.setdefault(key, []).append({"role": "user", "content": message})
        if self._db is not None:
            try:
                await self._db.save_session(key, "user", message)
            except Exception:
                logger.exception("持久化用户消息失败")

        self._trim_history(key)

        full_reply = ""
        try:
            async for chunk in self.adapter.chat_stream(
                messages=self._sessions[key],
                system_prompt=self._config.system_prompt,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            ):
                full_reply += chunk
                yield chunk
            if full_reply:
                self._sessions[key].append({"role": "assistant", "content": full_reply})
                if self._db is not None:
                    try:
                        await self._db.save_session(key, "assistant", full_reply)
                    except Exception:
                        logger.exception("持久化助手回复失败")
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                self._sessions[key].pop()
            yield "抱歉，AI 服务暂时不可用。"

    async def check_availability(self) -> bool:
        try:
            return await self.adapter.check_availability()
        except Exception:
            return False
