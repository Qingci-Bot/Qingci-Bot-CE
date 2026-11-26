# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""LLM 管理器 - 多模型管理、会话上下文、Token 裁剪、持久化

特性：
- 基于 LiteLLMAdapter 统一调用 100+ LLM 提供商
- 会话历史双写：内存缓存 + 数据库持久化（可选）
- 裁剪/摘要的**决策**由记忆提供方（内置 memory 插件）给出，本类只负责
  落库一致性；未注入提供方时退回保守硬裁剪（见 bot/llm/memory.py）
- 每轮对话前由提供方增强系统提示词（注入召回记忆），回复后异步观察沉淀
- Function Calling（llm.enable_tools）：chat_with_tools 多轮工具调用循环
- clear_session 为 async 方法，安全处理与进行中 chat 的并发竞态
- 同一 session key 的并发调用通过 asyncio.Lock 串行化
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Optional

from ..config import LLMConfig, SessionSummaryConfig
from ..core.tasks import spawn_background_task
from ..db.database import Database
from .adapter import LLMAdapter
from .litellm_adapter import LiteLLMAdapter
from .memory import FallbackMemoryProvider, MemoryProvider, SummaryPolicy

if TYPE_CHECKING:
    from .mcp import MCPBridge
    from .tools import ToolRegistry

logger = logging.getLogger("qingci-bot.llm.manager")


class LLMManager:
    """LLM 管理器：适配器管理、会话上下文、Token 裁剪"""

    def __init__(
        self,
        config: LLMConfig,
        db: Database | None = None,
        summary_config: SessionSummaryConfig | None = None,
        usage_tracking: bool = True,
    ):
        self._config = config
        self._db = db
        # 会话摘要配置（session_summary 节）：未传入时用默认值，
        # 保证 llm.enable_summary 单独开启时也有可用阈值
        self._summary_config = (
            summary_config if summary_config is not None else SessionSummaryConfig()
        )
        # 用量入库开关（log.usage_tracking）：关闭后不写 usage_logs
        self._usage_tracking = usage_tracking
        self._adapter: LLMAdapter | None = None
        # 最近一次可用性检查失败的原因（供 /llm/test 展示）
        self.last_error: str = ""
        # 内存会话缓存: key = "group:{group_id}:{user_id}" 或 "private:{user_id}"
        self._sessions: dict[str, list[dict]] = {}
        # 已从 DB 懒加载过的 session key
        self._loaded_sessions: set[str] = set()
        # 会话级锁：防止同一 session 的并发调用导致历史交叉
        # 注意：锁随 session 创建，clear_session 不弹出锁以保护进行中的 chat
        # 长期运行的 bot 可能累积大量锁，未来可引入 LRU 淘汰
        self._locks: dict[str, asyncio.Lock] = {}
        # 全局重载锁：串行化 reload/clear 对 _locks 的快照与 _sessions 的清空。
        # 若无此锁，reload 遍历 _locks.keys() 快照期间并发 chat 会新建会话锁，
        # 轻则 dict 并发修改报错，重则新建会话逃过 reload 的清空（内存历史残留）
        self._reload_lock = asyncio.Lock()
        # Function Calling 工具注册表（enable_tools 且非 None 时启用）
        self._tool_registry: ToolRegistry | None = None
        # MCP 桥接器（enable_tools + mcp_servers 配置时由 setup_mcp_tools 创建）
        self._mcp: MCPBridge | None = None
        # token 计数缓存：(role, content) -> token 数，避免对同一消息重复计数
        self._token_cache: dict[tuple[str, str], int] = {}
        # 模型能力判断缓存：模型不变时 supports_function_calling 结果恒定，
        # 避免每条消息都走 litellm.get_model_info；reload 时一并清空
        self._tools_support_cache: bool | None = None
        # 记忆提供方：裁剪/摘要策略、提示词增强、轮次观察都由它实现。
        # 默认是只做硬裁剪的兜底实现，内置 memory 插件在 on_load 时替换掉
        # （见 bot/llm/memory.py 的模块注释：框架只留接缝，能力在插件）。
        self._memory: MemoryProvider = FallbackMemoryProvider()

    # ============ 适配器管理 ============

    def _create_adapter(self) -> LLMAdapter:
        """根据配置创建 litellm 适配器（统一入口）"""
        return LiteLLMAdapter(
            provider=self._config.provider,
            api_url=self._config.api_url,
            api_key=self._config.api_key,
            model=self._config.model,
            timeout=self._config.timeout,
            num_retries=self._config.num_retries,
        )

    @property
    def adapter(self) -> LLMAdapter:
        if self._adapter is None:
            self._adapter = self._create_adapter()
        return self._adapter

    async def reload(
        self,
        config: LLMConfig,
        summary_config: SessionSummaryConfig | None = None,
        usage_tracking: bool | None = None,
    ):
        """重载 LLM 配置

        获取所有会话锁后再重置，避免与进行中的 chat 竞态。
        锁在重置完成后才释放，确保新的 chat 使用新配置。
        summary_config 非 None 时同步更新会话摘要配置；
        usage_tracking 非 None 时同步更新用量入库开关。
        """
        # 全局重载锁保护快照：期间不会新建会话锁，保证
        # _locks.keys() 快照与 _sessions 清空的一致性（见 __init__ 注释）
        async with self._reload_lock:
            # 获取所有现有会话锁（持有到方法结束）
            # 统一按 key 字典序加锁，与其他多锁路径保持一致，消除理论死锁
            locks = [self._locks[k] for k in sorted(self._locks.keys())]
            acquired: list[asyncio.Lock] = []
            for lock in locks:
                await lock.acquire()
                acquired.append(lock)
            try:
                old_model = self._config.model
                self._config = config
                if summary_config is not None:
                    self._summary_config = summary_config
                if usage_tracking is not None:
                    self._usage_tracking = usage_tracking
                # 模型可能已切换，旧 token 计数不再可靠，整体清空重建
                self._token_cache.clear()
                self._tools_support_cache = None
                if self._adapter is not None:
                    try:
                        await self._adapter.close()
                    except Exception:
                        logger.exception(f"关闭旧适配器失败: model={old_model}")
                self._adapter = None
                # 不清空内存会话：仅调整参数不应重置所有用户的对话上下文
                # （DB 持久化 + 未落库轮次继续保留，reload 后历史无缝延续）
                # 重建适配器
                self._adapter = self._create_adapter()
                logger.info("LLM 配置已重载")
            finally:
                for lock in acquired:
                    lock.release()

    async def close(self):
        """关闭 LLM 管理器，释放资源"""
        if self._adapter is not None:
            try:
                await self._adapter.close()
            except Exception:
                logger.exception(f"关闭 LLM 适配器失败: model={self._config.model}")
        self._adapter = None
        if self._mcp is not None:
            try:
                await self._mcp.close()
            except Exception:
                logger.exception("关闭 MCP 桥接器失败")
            self._mcp = None
        self._sessions.clear()
        self._loaded_sessions.clear()

    # ============ 会话管理 ============

    def _session_key(self, message_type: str, group_id: int, user_id: int) -> str:
        if message_type == "private":
            return f"private:{user_id}"
        return f"group:{group_id}:{user_id}"

    def _get_lock(self, key: str) -> asyncio.Lock:
        """获取会话级锁（同步内部方法，需在 reload 锁保护下调用）"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def _get_session_lock(self, key: str) -> asyncio.Lock:
        """获取会话锁（受全局重载锁保护）

        通过 reload 锁串行化 _locks 字典的读写，避免与 reload 遍历
        _locks.keys() 快照并发修改导致异常，也保证 reload 快照后
        新建的会话锁不会逃过其 _sessions 清空。
        """
        async with self._reload_lock:
            return self._get_lock(key)

    async def _clear_one(self, key: str) -> None:
        """清除单个会话（内存 + DB + 该会话的短期记忆），调用方须持有该会话锁

        在锁内同步清除内存与 DB，避免清除期间新的 chat 从 DB 重载出
        尚未删除的历史（内存与 DB 分裂导致历史"复活"）。
        """
        self._sessions.pop(key, None)
        self._loaded_sessions.discard(key)
        if self._db is not None:
            try:
                await self._db.clear_sessions(key)
            except Exception:
                logger.exception(f"清除 DB 会话失败: key={key}, model={self._config.model}")
        # 短期记忆随会话一起清空；长期/永久记忆属于用户资产，不在此清除
        # （要清需用 /memory 命令显式操作）
        try:
            await self._memory.forget(session_key=key)
        except Exception:
            logger.exception(f"清除会话短期记忆失败: key={key}")

    async def clear_session_by_key(self, key: str) -> None:
        """按 session key 清除单个会话（供 API 路由等外部调用）

        与 clear_session 走同一加锁路径并同步清空内存与 DB，
        避免仅删 DB 导致内存历史在下次对话时被继续使用（"复活"）。
        """
        lock = await self._get_session_lock(key)
        async with lock:
            await self._clear_one(key)

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
            self._sessions[key] = [{"role": r["role"], "content": r["content"]} for r in rows]
            self._loaded_sessions.add(key)
        except Exception:
            logger.exception(f"加载会话历史失败: key={key}, model={self._config.model}")
            self._sessions.setdefault(key, [])
            # 不标记 _loaded_sessions，允许下次重试

    # ============ Token 计数（带缓存） ============

    def _estimate_message_tokens(self, msg: dict) -> int:
        """估算单条消息的 token 数（结果缓存，避免重复计数）

        优先使用 litellm 精确计数；无可用 tokenizer 时降级为
        字符估算（中文≈ 2 token/字符，与旧裁剪逻辑一致）。
        """
        content = msg.get("content")
        if not isinstance(content, str):
            content = str(content) if content is not None else ""
        key = (msg.get("role", ""), content)
        cached = self._token_cache.get(key)
        if cached is not None:
            return cached
        try:
            import litellm

            tokens = litellm.token_counter(self._config.model, messages=[msg])
        except Exception:
            # 降级：粗略估算（中文≈ 2 token/字符）
            tokens = max(1, len(content) * 2)
        tokens += 4  # 每条消息的固定开销（role/分隔符等）
        # 缓存容量保护：超限时整体清空重建
        if len(self._token_cache) > 2048:
            self._token_cache.clear()
        self._token_cache[key] = tokens
        return tokens

    def _estimate_messages_tokens(self, msgs: list[dict]) -> int:
        """估算消息列表的总 token 数（逐条走缓存）"""
        return sum(self._estimate_message_tokens(m) for m in msgs)

    @staticmethod
    def _user_id_from_key(key: str) -> int:
        """从 session key 提取 user_id（末段），解析失败返回 0"""
        try:
            return int(key.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            return 0

    # ============ 历史裁剪（决策交给记忆提供方） ============

    def _summary_policy(self) -> SummaryPolicy:
        """把 ``session_summary`` 与 ``llm.enable_summary`` 翻译成策略值对象

        两个开关任一为真即启用摘要（历史上先后加了这两个开关，语义已合并），
        阈值类参数统一读 ``session_summary`` 节。
        """
        cfg = self._summary_config
        return SummaryPolicy(
            enabled=bool(cfg.enabled or self._config.enable_summary),
            max_messages=cfg.max_messages,
            max_tokens=cfg.max_tokens,
            keep_recent_turns=cfg.keep_recent_turns,
            summary_max_tokens=cfg.summary_max_tokens,
        )

    async def _rewrite_db_sessions(self, key: str, msgs: list[dict]) -> None:
        """用 ``msgs`` 整体重写会话记录（摘要压缩后条数对不上，只能重写）"""
        if self._db is None:
            return
        try:
            await self._db.clear_sessions(key)
            for m in msgs:
                content = m.get("content")
                if not isinstance(content, str):
                    content = str(content) if content is not None else ""
                await self._db.save_session(key, m.get("role", "user"), content)
        except Exception:
            logger.exception(
                f"会话历史重写失败（内存已更新，重启后将回退旧历史）: "
                f"key={key}, model={self._config.model}"
            )

    async def _trim_history(self, key: str) -> bool:
        """把「本次请求该带哪些历史」交给记忆提供方决定，并保持 DB 与内存一致

        裁剪/摘要策略属于记忆能力，由 ``MemoryProvider``（内置 memory 插件）
        决定；未注入提供方时退回 ``FallbackMemoryProvider`` 的保守硬裁剪。
        本方法只负责：调用提供方 → 更新内存 → 按决定让 DB 对齐。

        落库逻辑刻意留在框架侧：这是「会话表一致性」问题而非记忆策略问题。
        提供方只回答"用哪些消息、要不要整体重写"，由这里一次性完成 DB 写入，
        避免两处各写一份导致内存与 DB 分裂、历史"复活"。

        Returns:
            是否走了整体重写 DB 的路径（摘要压缩）。chat() 据此跳过本条用户
            消息的独立落库，避免重复写入。
        """
        msgs = self._sessions.get(key, [])
        if not msgs:
            return False

        decision = await self._memory.manage_history(
            session_key=key,
            messages=list(msgs),
            max_messages=self._config.max_history * 2,
            max_tokens=self._config.max_context_tokens,
            summary=self._summary_policy(),
            estimate=self._estimate_messages_tokens,
        )
        new_msgs = decision.messages
        # 内容与条数都没变就不必碰内存与 DB
        if not decision.rewrite and len(new_msgs) == len(msgs):
            return False

        self._sessions[key] = new_msgs

        if decision.rewrite:
            await self._rewrite_db_sessions(key, new_msgs)
            logger.info(
                f"会话历史已由记忆提供方重写: key={key}, "
                f"{len(msgs)} 条 -> {len(new_msgs)} 条, provider={self._memory_name()}"
            )
            return True

        if len(new_msgs) < len(msgs) and self._db is not None:
            try:
                await self._db.trim_sessions(key, len(new_msgs))
            except Exception:
                logger.exception(f"裁剪 DB 会话历史失败: key={key}, model={self._config.model}")
        return False

    # ============ 记忆提供方 ============

    def _memory_name(self) -> str:
        return str(getattr(self._memory, "name", type(self._memory).__name__))

    def set_memory_provider(self, provider: MemoryProvider | None) -> None:
        """挂载记忆提供方（内置 memory 插件在 on_load 时调用）

        传 None 恢复兜底实现。重复调用是幂等的——插件重载会重新注入同一个
        实例，无需先卸载。
        """
        if provider is None:
            self._memory = FallbackMemoryProvider()
            logger.info("记忆提供方已重置为兜底实现（仅硬裁剪）")
            return
        self._memory = provider
        logger.info(f"记忆提供方已挂载: {self._memory_name()}")

    @property
    def memory(self) -> MemoryProvider:
        return self._memory

    async def _augment_prompt(
        self,
        key: str,
        message_type: str,
        group_id: int,
        user_id: int,
        query: str,
        system_prompt: str,
    ) -> str:
        """让提供方往系统提示词里注入召回的记忆

        失败时原样返回：记忆是增强能力，读不出来就少记一点，绝不能因为
        记忆库损坏让用户彻底收不到回复。
        """
        try:
            return await self._memory.augment_prompt(
                session_key=key,
                user_id=user_id,
                group_id=group_id if message_type == "group" else 0,
                query=query,
                system_prompt=system_prompt,
            )
        except Exception:
            logger.exception(f"记忆增强失败，使用原始系统提示词: key={key}")
            return system_prompt

    def _observe_turn(
        self,
        key: str,
        message_type: str,
        group_id: int,
        user_id: int,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """把一轮对话交给提供方观察（后台执行，不阻塞回复）

        刻意 fire-and-forget：沉淀长期记忆可能要额外调一次模型，让用户等
        这个毫无必要；失败只记日志，不影响这一轮已生成的回复。
        """

        async def _run() -> None:
            try:
                await self._memory.observe_turn(
                    session_key=key,
                    user_id=user_id,
                    group_id=group_id if message_type == "group" else 0,
                    user_text=user_text,
                    assistant_text=assistant_text,
                )
            except Exception:
                logger.exception(f"记忆观察失败: key={key}, provider={self._memory_name()}")

        spawn_background_task(_run(), name="memory_observe")

    async def clear_session(self, message_type: str = "", group_id: int = 0, user_id: int = 0):
        """清除会话历史

        指定 message_type+user_id 清除单会话，全部参数缺省时清除全部。
        仅提供 message_type 而未提供 user_id 属参数错误，直接抛
        ValueError，避免误落入"清除全部"分支误删所有会话。
        改为 async 以安全处理并发 chat 调用：在清内存前获取对应会话锁。
        """
        if message_type and not user_id:
            raise ValueError("清除指定会话需提供 user_id")

        if message_type:
            key = self._session_key(message_type, group_id, user_id)
            # 获取会话锁后再清理，避免与进行中的 chat 竞态
            lock = await self._get_session_lock(key)
            async with lock:
                await self._clear_one(key)
            # 注意：不弹出 _locks[key]，避免进行中的 chat 丢失锁保护
        else:
            # 清除全部：全程持有 reload 锁，并快照 _locks（而非仅 _sessions）。
            #
            # 原实现只在快照时持锁、随后释放再逐个清理，存在两个分裂窗口：
            # 1) 快照后新建的会话不在列表里，内存未被清、DB 却被全清；
            # 2) 刚拿到锁、正从 DB 装载历史的首轮 chat（_sessions 还没有该 key）
            #    会把已删除的旧历史装回内存，之后继续追加——"历史复活"。
            # 快照 _locks + 全程持锁：清理期间不会有新锁被创建（_get_session_lock
            # 需 reload 锁）；已持锁的进行中 chat 会先完成（其写入先于下方
            # DB 全清），随后被一并清掉。reload 锁内无人反向等待（chat 只在
            # 取锁瞬间短暂持有 reload 锁），按字典序加锁也无自环风险。
            async with self._reload_lock:
                items = [(k, self._locks[k]) for k in sorted(self._locks.keys())]
                for k, lock in items:
                    async with lock:
                        self._sessions.pop(k, None)
                        self._loaded_sessions.discard(k)
                        try:
                            await self._memory.forget(session_key=k)
                        except Exception:
                            logger.exception(f"清除会话短期记忆失败: key={k}")
                if self._db is not None:
                    try:
                        await self._db.clear_sessions(None)
                    except Exception:
                        logger.exception(f"清除全部 DB 会话失败: model={self._config.model}")

    # ============ Function Calling ============

    def set_tool_registry(self, registry: Optional["ToolRegistry"]) -> None:
        """挂载工具注册表（由 Bot 装配阶段调用）"""
        self._tool_registry = registry

    async def setup_mcp_tools(self) -> None:
        """初始化 MCP 工具（enable_tools 且配置了 mcp_servers 时）

        - 连接各 MCP 服务器，将工具注册进 ToolRegistry（mcp_ 前缀）
        - mcp 包未安装、连接失败仅记录日志，不阻断启动
        - 重复调用前先清理旧注册，保证幂等
        """
        if not self._config.enable_tools:
            return
        servers = getattr(self._config, "mcp_servers", None) or []
        if not servers:
            return
        # 清理上次注册的 MCP 工具（防重复 start 残留）
        if self._tool_registry is not None:
            self._tool_registry.unregister_by_prefix("mcp_")
        bridge = None
        try:
            from .mcp import MCPBridge

            bridge = MCPBridge()
            connected = await bridge.connect_servers(servers)
            if connected and self._tool_registry is not None:
                count = await bridge.register_tools(self._tool_registry)
                self._mcp = bridge
                bridge = None  # 转移所有权，不再在 finally 中关闭
                logger.info(
                    f"MCP 工具初始化完成: {count} 个（服务器: "
                    f"{', '.join(self._mcp.connected_servers)}）"
                )
        except Exception:
            logger.exception("MCP 工具初始化失败")
            if self._mcp is not None:
                try:
                    await self._mcp.close()
                except Exception:
                    logger.exception("关闭 MCP 桥接器失败")
                self._mcp = None
        finally:
            # 连接成功但注册失败 / 连接失败等场景，关闭本次新建的 bridge
            if bridge is not None:
                try:
                    await bridge.close()
                except Exception:
                    logger.exception("关闭未注册的 MCP 桥接器失败")

    @property
    def tool_registry(self) -> Optional["ToolRegistry"]:
        return self._tool_registry

    def _model_supports_tools(self) -> bool:
        """检查当前模型是否声明支持 Function Calling

        结果按模型缓存（模型切换时由 reload 清空），避免每条消息
        都调用 litellm.get_model_info 造成额外开销。
        未知模型（如自定义 OpenAI 兼容服务）视为支持，
        实际不支持时 LLM 会返回错误并由上层降级处理。
        """
        if self._tools_support_cache is not None:
            return self._tools_support_cache
        try:
            import litellm

            model = getattr(self._adapter, "model", None) or self._config.model
            info = litellm.get_model_info(model)
            self._tools_support_cache = bool(info.get("supports_function_calling", True))
        except Exception:
            self._tools_support_cache = True
        return self._tools_support_cache

    @staticmethod
    def _tool_call_field(tc, field: str, default=None):
        """兼容对象属性/字典两种 tool_call 结构的字段读取"""
        if isinstance(tc, dict):
            return tc.get(field, default)
        return getattr(tc, field, default)

    async def chat_with_tools(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        images: list[str] | None = None,
    ) -> tuple[str, dict | None]:
        """Function Calling 循环：LLM 返回 tool_calls 时执行工具并回传结果

        - 最大轮数由 llm.max_tool_rounds 控制，防止模型反复调用工具导致死循环
        - 工具执行异常由 ToolRegistry.execute 捕获，以错误文本回传给模型
        - 工具轮次的中间消息仅存于工作副本，不写入会话历史/DB
        - 多轮 usage 累加后统一返回，由调用方入库

        Returns:
            (回复文本, 累计 usage dict 或 None)
        """
        registry = self._tool_registry
        tools = registry.get_openai_tools() if registry is not None else None
        working: list[dict] = list(messages)
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        has_usage = False

        def _accumulate(usage: dict | None) -> None:
            nonlocal has_usage
            if usage:
                has_usage = True
                total_usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
                total_usage["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)

        # 工具轮次：配置的轮数扣除 1 轮收尾（强制文本），至少保留 1 轮工具调用，
        # 避免 max_tool_rounds=1 时唯一一轮被误判为收尾而完全不下发 tools
        configured_rounds = max(1, self._config.max_tool_rounds)
        tool_rounds = max(1, configured_rounds - 1)
        max_rounds = tool_rounds + 1  # 工具轮 + 收尾轮
        for round_idx in range(max_rounds):
            # 最后一轮不再下发 tools，强制模型产出文本回复
            round_tools = tools if round_idx < tool_rounds else None
            result = await self.adapter.chat_detail(
                messages=working,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=round_tools,
                images=images if round_idx == 0 else None,
            )
            _accumulate(result.usage)
            images = None  # 图片仅首轮携带，避免重复计入多模态开销

            tool_calls = result.tool_calls or []
            if not tool_calls:
                return result.content, (total_usage if has_usage else None)

            # 将 assistant 的 tool_calls 与工具执行结果追加到工作副本
            assistant_msg: dict = {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [
                    {
                        "id": self._tool_call_field(tc, "id", "") or "",
                        "type": "function",
                        "function": {
                            "name": self._tool_call_field(
                                self._tool_call_field(tc, "function", {}), "name", ""
                            )
                            or "",
                            "arguments": self._tool_call_field(
                                self._tool_call_field(tc, "function", {}), "arguments", "{}"
                            )
                            or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
            working.append(assistant_msg)

            for tc in tool_calls:
                func = self._tool_call_field(tc, "function", {}) or {}
                name = self._tool_call_field(func, "name", "") or ""
                raw_args = self._tool_call_field(func, "arguments", "{}") or "{}"
                tc_id = self._tool_call_field(tc, "id", "") or ""
                # 防御：registry 为 None 时不应进入工具执行（如 LLM 意外返回 tool_calls）
                if registry is None:
                    logger.warning(f"跳过工具调用（registry 未就绪）: name={name}")
                    break
                try:
                    arguments = (
                        json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    )
                    if not isinstance(arguments, dict):
                        arguments = {}
                except (json.JSONDecodeError, TypeError, ValueError):
                    logger.warning(f"工具参数解析失败: name={name}, args={raw_args!r}")
                    arguments = {}
                logger.info(f"执行工具调用: name={name}, round={round_idx + 1}")
                # ToolRegistry.execute 内部捕获一切异常，返回错误文本回传模型
                output = await registry.execute(name, arguments)
                working.append({"role": "tool", "tool_call_id": tc_id, "content": output})

        # 达到最大轮数仍未产出文本（收尾轮模型仍返回 tool_calls）：直接取其内容，
        # 空则给占位回复——避免再一次无谓的 LLM 调用（浪费 token/延迟）
        content = (result.content or "").strip()
        if not content:
            logger.warning(
                f"工具调用达到最大轮数 {max_rounds}，模型未产出文本，返回占位回复: "
                f"model={self._config.model}"
            )
            content = "（工具调用已达上限，未生成回复）"
        return content, (total_usage if has_usage else None)

    # ============ 对话调用 ============

    async def chat(
        self,
        message: str,
        message_type: str = "group",
        group_id: int = 0,
        user_id: int = 0,
        user_name: str = "",  # 保留兼容：历史参数，当前实现不读取，外部插件仍可能传入
        images: list[str] | None = None,
        system_prompt: str | None = None,
        source: str = "chat",
    ) -> str | None:
        """同步聊天（返回完整回复，失败时返回 None）

        Args:
            system_prompt: 可选覆盖系统提示词，None 时回退配置中的 system_prompt
            source: 用量来源标记（chat/tool/summary/image），供后续功能复用
        """
        key = self._session_key(message_type, group_id, user_id)
        lock = await self._get_session_lock(key)
        async with lock:
            await self._ensure_session_loaded(key)

            # 追加用户消息
            self._sessions.setdefault(key, []).append({"role": "user", "content": message})

            # 裁剪上下文（异步：开关开启且超阈时触发摘要压缩）
            summarized = await self._trim_history(key)

            # 并行持久化用户消息：与下方 LLM 网络调用同时进行（DB 写不阻塞请求）。
            # 摘要压缩已把本条用户消息随 recent_msgs 重写落库，此处跳过避免重复写入
            save_user_task = None
            if self._db is not None and not summarized:
                save_user_task = asyncio.create_task(self._db.save_session(key, "user", message))

            async def _wait_user_saved() -> bool:
                """等待用户消息落库，返回是否成功（失败仅记日志）"""
                if save_user_task is None:
                    return False
                try:
                    await save_user_task
                    return True
                except Exception:
                    logger.exception(f"持久化用户消息失败: key={key}, model={self._config.model}")
                    return False

            # 调用 LLM（走 chat_detail 以获取 usage，供后续用量统计）
            # Function Calling：仅在开关开启、注册表就绪且模型支持 tools 时启用
            use_tools = (
                self._config.enable_tools
                and self._tool_registry is not None
                and self._tool_registry.names()
                and self._model_supports_tools()
            )
            # 记忆增强：由提供方把召回的记忆注入系统提示词（无内容时原样返回）
            effective_prompt = await self._augment_prompt(
                key,
                message_type,
                group_id,
                user_id,
                message,
                system_prompt if system_prompt is not None else self._config.system_prompt,
            )
            try:
                if use_tools:
                    reply, usage = await self.chat_with_tools(
                        messages=self._sessions[key],
                        system_prompt=effective_prompt,
                        max_tokens=self._config.max_tokens,
                        temperature=self._config.temperature,
                        images=images,
                    )
                else:
                    result = await self.adapter.chat_detail(
                        messages=self._sessions[key],
                        system_prompt=effective_prompt,
                        max_tokens=self._config.max_tokens,
                        temperature=self._config.temperature,
                        images=images,
                    )
                    reply = result.content
                    usage = result.usage
                # 用量入库（fire-and-forget）：失败仅记日志，不阻断主链路；
                # 受 log.usage_tracking 开关控制（可退出的遥测）
                if self._db is not None and usage and self._usage_tracking:
                    spawn_background_task(
                        self._db.save_usage(
                            session_key=key,
                            user_id=user_id,
                            model=self._config.model,
                            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                            source=source,
                        ),
                        name="save_usage",
                    )
            except asyncio.CancelledError:
                # 调用被取消：同样回滚刚加入的用户消息，保持内存与 DB 一致，
                # 再重新抛出（与 chat_stream 的取消语义一致）
                logger.warning(
                    f"LLM 调用被取消，回滚用户消息: key={key}, model={self._config.model}"
                )
                if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                    self._sessions[key].pop()
                    db_saved = await _wait_user_saved()
                    if db_saved and self._db is not None:
                        try:
                            await self._db.delete_last_session(key, "user")
                        except Exception:
                            logger.exception(
                                f"回滚用户消息失败: key={key}, model={self._config.model}"
                            )
                raise
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}, key={key}, model={self._config.model}")
                # 回滚刚加入的用户消息，保持内存与 DB 一致
                if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                    self._sessions[key].pop()
                    db_saved = await _wait_user_saved()
                    if db_saved and self._db is not None:
                        try:
                            await self._db.delete_last_session(key, "user")
                        except Exception:
                            logger.exception(
                                f"回滚用户消息失败: key={key}, model={self._config.model}"
                            )
                return None
            finally:
                # 兜底：父任务被取消等异常路径下，确保用户消息落库任务被回收
                if save_user_task is not None and not save_user_task.done():
                    await asyncio.gather(save_user_task, return_exceptions=True)

            # 空回复：回滚用户消息（与 chat_stream 语义保持一致），
            # 不 save_session/append，避免历史中出现空 assistant 回复
            if not reply:
                if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                    self._sessions[key].pop()
                    db_saved = await _wait_user_saved()
                    if db_saved and self._db is not None:
                        try:
                            await self._db.delete_last_session(key, "user")
                        except Exception:
                            logger.exception(
                                f"回滚用户消息失败: key={key}, model={self._config.model}"
                            )
                logger.warning(
                    f"LLM 返回空回复，已回滚用户消息: key={key}, model={self._config.model}"
                )
                return None

            # 保存 assistant 回复（先等用户消息落库保证 DB 写入顺序，再写回复）
            if self._db is not None:
                await _wait_user_saved()
                try:
                    await self._db.save_session(key, "assistant", reply)
                except Exception:
                    logger.exception(f"持久化助手回复失败: key={key}, model={self._config.model}")
                    # DB 失败时仍保留内存（容错），记录不一致
            self._sessions.setdefault(key, []).append({"role": "assistant", "content": reply})
            # 记忆观察（后台）：沉淀长期/永久记忆、强化显著性、演化。
            # 放在历史已完整落库之后，保证提供方看到的是最终成功的一轮。
            self._observe_turn(key, message_type, group_id, user_id, message, reply)
            return reply

    async def chat_stream(
        self,
        message: str,
        message_type: str = "group",
        group_id: int = 0,
        user_id: int = 0,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式聊天（逐段返回增量文本）

        Args:
            system_prompt: 可选覆盖系统提示词，None 时回退配置中的 system_prompt
                （与 chat() 对齐；ChatLab 经此传入人设/能力/RAG 组装结果，
                保证调试会话与真实对话行为一致）

        注意：流式路径暂不统计用量——litellm 流式响应需额外开启
        stream_options 才能拿到 usage，待后续批次单独处理。
        """
        key = self._session_key(message_type, group_id, user_id)
        lock = await self._get_session_lock(key)
        async with lock:
            await self._ensure_session_loaded(key)

            self._sessions.setdefault(key, []).append({"role": "user", "content": message})

            await self._trim_history(key)

            # 并行持久化用户消息：与下方 LLM 流式调用同时进行
            save_user_task = None
            if self._db is not None:
                save_user_task = asyncio.create_task(self._db.save_session(key, "user", message))

            async def _wait_user_saved() -> bool:
                """等待用户消息落库，返回是否成功（失败仅记日志）"""
                if save_user_task is None:
                    return False
                try:
                    await save_user_task
                    return True
                except Exception:
                    logger.exception(f"持久化用户消息失败: key={key}, model={self._config.model}")
                    return False

            full_reply = ""
            success = False
            cancelled = False
            gen_exit = False
            # 记忆增强：流式同样注入召回的记忆（无内容时原样返回）；
            # system_prompt 覆盖值优先（与 chat() 语义一致）
            effective_prompt = await self._augment_prompt(
                key,
                message_type,
                group_id,
                user_id,
                message,
                system_prompt if system_prompt is not None else self._config.system_prompt,
            )
            try:
                async for chunk in self.adapter.chat_stream(
                    messages=self._sessions[key],
                    system_prompt=effective_prompt,
                    max_tokens=self._config.max_tokens,
                    temperature=self._config.temperature,
                ):
                    full_reply += chunk
                    yield chunk
                success = True
            except GeneratorExit:
                # 消费者提前停止迭代，需要回滚用户消息
                # 置标志并继续执行下方清理逻辑，清理完成后重抛 GeneratorExit，
                # 与 CancelledError 的“清理后重抛”模式保持一致
                # 注意：捕获 GeneratorExit 后不能再 yield（会抛 RuntimeError）
                gen_exit = True
                success = False
            except asyncio.CancelledError:
                # 任务被取消：标记失败，由下方清理逻辑回滚用户消息后重新抛出
                cancelled = True
                success = False
            except Exception as e:
                logger.error(f"LLM 流式调用失败: {e}, key={key}, model={self._config.model}")
                # 不 yield 错误信息，避免污染内容流

            # 清理逻辑：依据 success 标志决定保存回复还是回滚用户消息
            try:
                if success and full_reply:
                    # 保存 assistant 回复（先等用户消息落库保证 DB 顺序，再写）
                    if self._db is not None:
                        await _wait_user_saved()
                        try:
                            await self._db.save_session(key, "assistant", full_reply)
                        except Exception:
                            logger.exception(
                                f"持久化助手回复失败: key={key}, model={self._config.model}"
                            )
                            # DB 失败时仍保留内存（容错），记录不一致
                    self._sessions.setdefault(key, []).append(
                        {"role": "assistant", "content": full_reply}
                    )
                    # 记忆观察（后台）：与 chat() 一致，只在成功产出回复后触发
                    self._observe_turn(key, message_type, group_id, user_id, message, full_reply)
                elif not success:
                    # 失败（含取消）时回滚用户消息
                    if self._sessions[key] and self._sessions[key][-1]["role"] == "user":
                        self._sessions[key].pop()
                        db_saved = await _wait_user_saved()
                        if db_saved and self._db is not None:
                            try:
                                await self._db.delete_last_session(key, "user")
                            except Exception:
                                logger.exception(
                                    f"回滚用户消息失败: key={key}, model={self._config.model}"
                                )
                elif success and not full_reply:
                    # 空回复：回滚用户消息，避免历史中出现空 assistant 回复
                    if self._sessions.get(key) and self._sessions[key][-1]["role"] == "user":
                        self._sessions[key].pop()
                        db_saved = await _wait_user_saved()
                        if db_saved and self._db is not None:
                            try:
                                await self._db.delete_last_session(key, "user")
                            except Exception:
                                logger.exception(
                                    f"回滚用户消息失败: key={key}, model={self._config.model}"
                                )
            finally:
                # 清理完成后重新抛出取消/关闭异常，保持 asyncio 取消与生成器关闭语义
                if cancelled:
                    raise
                if gen_exit:
                    raise GeneratorExit

    async def check_availability(self) -> bool:
        try:
            ok = await self.adapter.check_availability()
            # 透传适配器记录的失败原因（供 /llm/test 等展示）
            self.last_error = getattr(self.adapter, "last_error", "")
            return ok
        except Exception:
            return False
