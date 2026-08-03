"""LLM 管理器 - 多模型管理、会话上下文、Token 裁剪、持久化

特性：
- 基于 LiteLLMAdapter 统一调用 100+ LLM 提供商
- 会话历史双写：内存缓存 + 数据库持久化（可选）
- 按 max_history 条数与 max_context_tokens 双重裁剪
- clear_session 为 async 方法，安全处理与进行中 chat 的并发竞态
- 同一 session key 的并发调用通过 asyncio.Lock 串行化
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
        # 会话级锁：防止同一 session 的并发调用导致历史交叉
        # 注意：锁随 session 创建，clear_session 不弹出锁以保护进行中的 chat
        # 长期运行的 bot 可能累积大量锁，未来可引入 LRU 淘汰
        self._locks: dict[str, asyncio.Lock] = {}

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
        """重载 LLM 配置

        获取所有会话锁后再重置，避免与进行中的 chat 竞态。
        锁在重置完成后才释放，确保新的 chat 使用新配置。
        """
        # 获取所有现有会话锁（持有到方法结束）
        locks = [self._locks[k] for k in list(self._locks.keys())]
        for lock in locks:
            await lock.acquire()
        try:
            self._config = config
            if self._adapter is not None:
                try:
                    await self._adapter.close()
                except Exception:
                    logger.exception("关闭旧适配器失败")
            self._adapter = None
            self._sessions.clear()
            self._loaded_sessions.clear()
            # 重建适配器
            self._adapter = self._create_adapter()
            logger.info("LLM 配置已重载")
        finally:
            for lock in locks:
                lock.release()

    async def close(self):
        """关闭 LLM 管理器，释放资源"""
        if self._adapter is not None:
            try:
                await self._adapter.close()
            except Exception:
                logger.exception("关闭 LLM 适配器失败")
        self._adapter = None
        self._sessions.clear()
        self._loaded_sessions.clear()

    # ============ 会话管理 ============

    def _session_key(self, message_type: str, group_id: int, user_id: int) -> str:
        if message_type == "private":
            return f"private:{user_id}"
        return f"group:{group_id}:{user_id}"

    def _get_lock(self, key: str) -> asyncio.Lock:
        """获取会话级锁"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def _ensure_session_loaded(self, key: str):
        """懒加载：首次访问某会话时从 DB 读取历史"""
        if key in self._loaded_sessions:
            return
        if self._db is None:
            self._sessions.setdefault(key, [])
            self._loaded_sessions.add(key)
            return
        try:
            rows = await self._db.get_sessions(key, limit=self._config.max_history * 2)
            self._sessions[key] = [
                {"role": r["role"], "content": r["content"]} for r in rows
            ]
            self._loaded_sessions.add(key)
        except Exception:
            logger.exception(f"加载会话历史失败: {key}")
            self._sessions.setdefault(key, [])
            # 不标记 _loaded_sessions，允许下次重试

    def _trim_history(self, key: str):
        """裁剪会话历史，确保不超过 max_history 和 max_context_tokens

        注意：仅裁剪内存中的历史。DB 中的旧记录由 _ensure_session_loaded
        的 limit 参数限制加载，不会影响上下文。定期 clear_session 可清理 DB。
        """
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
        if max_tokens <= 0 or len(msgs) <= 2:
            return
        try:
            import litellm
            total = litellm.token_counter(self._config.model, messages=msgs)
            # 若已超限，逐条裁剪（避免每次 pop 后全量重算）
            while len(msgs) > 2 and total > max_tokens:
                removed = msgs.pop(0)
                # 增量减去被移除消息的 token（粗略估算）
                removed_tokens = litellm.token_counter(
                    self._config.model,
                    messages=[removed],
                )
                total -= removed_tokens
            self._sessions[key] = msgs
        except Exception:
            # 降级：粗略估算（中文≈2 token/字符）
            while len(msgs) > 2 and sum(
                len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                for m in msgs
            ) * 2 > max_tokens:
                msgs.pop(0)

    async def clear_session(
        self, message_type: str = "", group_id: int = 0, user_id: int = 0
    ):
        """清除会话历史

        指定参数清除单会话，不指定参数清除全部。
        改为 async 以安全处理并发 chat 调用：在清内存前获取对应会话锁。
        """
        if message_type and user_id:
            key = self._session_key(message_type, group_id, user_id)
            # 获取会话锁后再清理，避免与进行中的 chat 竞态
            lock = self._get_lock(key)
            async with lock:
                self._sessions.pop(key, None)
                self._loaded_sessions.discard(key)
            # 注意：不弹出 _locks[key]，避免进行中的 chat 丢失锁保护
            # 异步清除 DB
            if self._db is not None:
                try:
                    await self._db.clear_sessions(key)
                except Exception:
                    logger.exception(f"清除 DB 会话失败: {key}")
        else:
            # 清除全部：获取所有锁
            keys = list(self._sessions.keys())
            for k in keys:
                lock = self._get_lock(k)
                async with lock:
                    self._sessions.pop(k, None)
                    self._loaded_sessions.discard(k)
            # 不清空 _locks，避免进行中的 chat 丢失锁保护
            if self._db is not None:
                try:
                    await self._db.clear_sessions(None)
                except Exception:
                    logger.exception("清除全部 DB 会话失败")

    # ============ 对话调用 ============

    async def chat(
        self,
        message: str,
        message_type: str = "group",
        group_id: int = 0,
        user_id: int = 0,
        user_name: str = "",
        images: Optional[list[str]] = None,
    ) -> Optional[str]:
        """同步聊天（返回完整回复，失败时返回 None）"""
        key = self._session_key(message_type, group_id, user_id)
        lock = self._get_lock(key)
        async with lock:
            await self._ensure_session_loaded(key)

            # 追加用户消息
            self._sessions.setdefault(key, []).append({"role": "user", "content": message})
            # 持久化用户消息
            db_saved = False
            if self._db is not None:
                try:
                    await self._db.save_session(key, "user", message)
                    db_saved = True
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
                # 回滚刚加入的用户消息，保持内存与 DB 一致
                if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                    self._sessions[key].pop()
                    if db_saved:
                        try:
                            await self._db.delete_last_session(key, "user")
                        except Exception:
                            logger.exception("回滚用户消息失败")
                return None

            # 保存 assistant 回复（先 DB 后内存，保持一致性）
            if self._db is not None:
                try:
                    await self._db.save_session(key, "assistant", reply)
                except Exception:
                    logger.exception("持久化助手回复失败")
                    # DB 失败时仍保留内存（容错），记录不一致
            self._sessions.setdefault(key, []).append({"role": "assistant", "content": reply})
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
        lock = self._get_lock(key)
        async with lock:
            await self._ensure_session_loaded(key)

            self._sessions.setdefault(key, []).append({"role": "user", "content": message})
            db_saved = False
            if self._db is not None:
                try:
                    await self._db.save_session(key, "user", message)
                    db_saved = True
                except Exception:
                    logger.exception("持久化用户消息失败")

            self._trim_history(key)

            full_reply = ""
            success = False
            try:
                async for chunk in self.adapter.chat_stream(
                    messages=self._sessions[key],
                    system_prompt=self._config.system_prompt,
                    max_tokens=self._config.max_tokens,
                    temperature=self._config.temperature,
                ):
                    full_reply += chunk
                    yield chunk
                success = True
            except GeneratorExit:
                # 消费者提前停止迭代，需要回滚用户消息
                # 捕获后不再 re-raise，让清理逻辑执行后正常结束
                # 注意：捕获 GeneratorExit 后不能再 yield（会抛 RuntimeError）
                success = False
            except Exception as e:
                logger.error(f"LLM 流式调用失败: {e}")
                # 不 yield 错误信息，避免污染内容流

            # 仅在成功完成时保存完整回复，避免部分回复污染上下文
            if success and full_reply:
                # 保存 assistant 回复（先 DB 后内存，保持一致性）
                if self._db is not None:
                    try:
                        await self._db.save_session(key, "assistant", full_reply)
                    except Exception:
                        logger.exception("持久化助手回复失败")
                        # DB 失败时仍保留内存（容错），记录不一致
                self._sessions.setdefault(key, []).append({"role": "assistant", "content": full_reply})
            elif not success:
                # 失败时回滚用户消息
                if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                    self._sessions[key].pop()
                    if db_saved:
                        try:
                            await self._db.delete_last_session(key, "user")
                        except Exception:
                            logger.exception("回滚用户消息失败")
            elif success and not full_reply:
                # 空回复：回滚用户消息，避免历史中出现空 assistant 回复
                if self._sessions.get(key) and self._sessions[key][-1]["role"] == "user":
                    self._sessions[key].pop()
                    if db_saved:
                        try:
                            await self._db.delete_last_session(key, "user")
                        except Exception:
                            logger.exception("回滚用户消息失败")

    async def check_availability(self) -> bool:
        try:
            return await self.adapter.check_availability()
        except Exception:
            return False
