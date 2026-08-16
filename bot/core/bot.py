"""Bot 主类 - 生命周期管理、组件编排"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..config import ConfigManager
from ..db import Database
from ..llm import LLMManager, ToolRegistry, register_builtin_tools
from ..paths import app_root, data_root
from ..plugin import PluginManager, PluginStatus
from ..plugin.ratelimit import RateLimiter
from ..plugin.watcher import PluginWatcher
from ..rag import KnowledgeStore
from .alerter import AlertHandler
from .connection import OneBotConnection
from .di import DIContainer
from .dispatcher import MessageContext, MessageDispatcher
from .event_bus import EventBus
from .filter import SensitiveFilter
from .scheduler import BotScheduler
from .session_state import SessionStateManager
from .tasks import spawn_background_task

logger = logging.getLogger("qingci-bot")

# 事件处理并发上限：防止消息洪峰时并发任务无界堆积（超限任务排队等待）
_EVENT_CONCURRENCY = 16
# 事件排队上限：_handle_event 中待处理（排队+执行）任务超过该值时丢弃新事件，
# 防止洪峰时任务无界堆积导致内存耗尽（此时丢弃比拖垮整个进程更可接受）
_MAX_PENDING_EVENTS = 128


class QingciBot:
    """Qingci-Bot CE 主类"""

    def __init__(self, config_path: str | None = None):
        path = Path(config_path) if config_path else None
        self.config = ConfigManager(path)
        self.config.load()

        self.db = Database()
        self.connection = OneBotConnection(
            host=self.config.onebot.host,
            port=self.config.onebot.port,
            access_token=self.config.onebot.access_token,
        )
        self.dispatcher = MessageDispatcher()
        self.llm = LLMManager(
            self.config.llm,
            db=self.db,
            summary_config=self.config.session_summary,
            usage_tracking=self.config.log.usage_tracking,
        )
        self.plugin_manager = PluginManager()

        # 依赖注入容器：集中管理所有服务实例
        self.di = DIContainer()
        # 会话状态：TTL 键值存储，用于多步骤对话、表单、临时缓存
        self.session_state = SessionStateManager()
        # 事件总线：跨插件发布-订阅事件广播，解耦插件间协作
        self.event_bus = EventBus()

        # 注册所有服务到 DI 容器（sync 兼容 __init__ 同步上下文）
        self.di.register_sync(ConfigManager, self.config)
        self.di.register_sync(Database, self.db)
        self.di.register_sync(OneBotConnection, self.connection)
        self.di.register_sync(LLMManager, self.llm)
        self.di.register_sync(PluginManager, self.plugin_manager)
        self.di.register_sync(MessageDispatcher, self.dispatcher)
        self.di.register_sync(SessionStateManager, self.session_state)
        self.di.register_sync(EventBus, self.event_bus)
        self.di.register_sync(DIContainer, self.di)
        # 注册自身（使插件可以注入 bot 引用）
        self.di.register_sync(QingciBot, self)

        self._running = False
        self._started = False  # 是否已调用过 start()（含部分失败场景）
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        # 事件处理并发信号量：限制同时执行的事件数，洪峰时超出部分排队等待
        self._event_sem = asyncio.Semaphore(_EVENT_CONCURRENCY)
        # 错误告警处理器（alert.enabled 时 start 中 attach，stop 中 detach）
        self._alert_handler: AlertHandler | None = None
        # 全局事件钩子（消息中间件）：横切统计/审计/预处理，
        # 默认为空列表（零行为变化），供插件/扩展注册
        self._pre_event_hooks: list[Any] = []
        self._post_event_hooks: list[Any] = []
        # Matcher 运行前全局钩子（run_preprocessor）：在 Matcher 匹配成功、
        # handler 运行前触发，用于横切鉴权/审计/改写上下文；返回非 None 则
        # 拦截该 Matcher（返回值作为回复并停止分发）。区别于事件级
        # _pre_event_hooks（事件级、Matcher 调度前）与插件级 before_handler
        # （插件内、handler 前）。默认为空列表。
        self._matcher_preprocessors: list[Any] = []

        # ---- 功能增强组件 ----
        # 批次 1：限流器实例（rate_limit.enabled 时创建；创建于核心层，
        # 不再依赖某个内置插件，避免插件缺失时限流静默失效）
        rl_cfg = getattr(self.config, "rate_limit", None)
        self.rate_limiter = None
        if rl_cfg is not None and rl_cfg.enabled:
            self.rate_limiter = RateLimiter(
                daily_limit=rl_cfg.daily_limit,
                cooldown_seconds=rl_cfg.cooldown_seconds,
            )
        # 批次 1：定时任务调度器（start/stop 中启动与关闭）
        self.scheduler = BotScheduler()
        # 插件开发期自动热重载监听器（hot_reload.enabled 时 start 中启动，stop 中关闭）
        self._plugin_watcher: PluginWatcher | None = None
        # 批次 3：Function Calling 工具注册表（常驻创建，轻量；
        # 仅在 llm.enable_tools 开启且模型支持 tools 时才实际参与调用）
        self.tool_registry = ToolRegistry()
        register_builtin_tools(self.tool_registry)
        self.llm.set_tool_registry(self.tool_registry)
        # 批次 3：知识库（rag.enabled 时创建，未启用时为 None）
        self.knowledge_store = None
        if self.config.rag.enabled:
            rag_cfg = self.config.rag
            knowledge_dir = Path(rag_cfg.knowledge_dir)
            if not knowledge_dir.is_absolute():
                knowledge_dir = data_root() / knowledge_dir
            llm_cfg = self.config.llm
            self.knowledge_store = KnowledgeStore(
                root=knowledge_dir,
                mode=rag_cfg.mode,
                chunk_size=rag_cfg.chunk_size,
                chunk_overlap=rag_cfg.chunk_overlap,
                top_k=rag_cfg.top_k,
                embedding_model=rag_cfg.embedding_model,
                embedding_api_url=rag_cfg.embedding_api_url or llm_cfg.api_url,
                embedding_api_key=rag_cfg.embedding_api_key or llm_cfg.api_key,
                collection_name=rag_cfg.collection_name,
            )
        # 敏感词过滤器：本批实例化，词库路径相对项目根目录解析
        # （enabled 开关由批次 1 的拦截逻辑判断，此处仅构造）
        words_file = Path(self.config.filter.words_file)
        if not words_file.is_absolute():
            words_file = data_root() / words_file
        self.sensitive_filter = SensitiveFilter(words_file)

    # ============ 生命周期 ============

    async def start(self) -> None:
        """启动 Bot"""
        self._started = True
        logger.info("Qingci-Bot CE 启动中...")
        await self.db.connect()
        await self.plugin_manager.load_builtin(self)
        # 加载外部插件目录（app_root/plugins/，exe 打包后同样生效）
        await self.plugin_manager.load_external_dir(self)
        # 应用全局语言到插件 i18n
        self.plugin_manager.set_i18n_locale(self.config.config.lang)
        # 初始化 MCP 工具（enable_tools + mcp_servers 配置时；失败仅记日志）
        await self.llm.setup_mcp_tools()
        self.connection.on_event(self._handle_event)
        # 注册全局生命周期回调：连接建立 → on_bot_connect，元事件 → on_metaevent
        self.connection.on_connect(self._on_bot_connect)
        self.connection.on_metaevent(self._on_metaevent)
        try:
            await self.connection.start()
        except (Exception, asyncio.CancelledError):
            # 连接启动失败或被取消（如 API 层 wait_for 超时），清理已初始化的资源
            try:
                await self.plugin_manager.shutdown()
            except (Exception, asyncio.CancelledError):
                logger.exception("清理插件失败")
            try:
                await self.db.close()
            except (Exception, asyncio.CancelledError):
                logger.exception("清理数据库失败")
            raise

        # 连接就绪后立即标记运行状态，避免事件到达时被 _handle_event 静默丢弃
        self._running = True

        # 启动调度器与错误告警；失败时连同连接/插件/数据库一并回滚
        try:
            if self.config.scheduler.enabled:
                self.scheduler.start()
            if self.config.alert.enabled:
                self._alert_handler = AlertHandler()
                self._alert_handler.attach(logger, self.connection, self.config)
            if self.config.hot_reload.enabled:
                self._plugin_watcher = PluginWatcher(
                    manager=self.plugin_manager,
                    bot=self,
                    directory=app_root() / "plugins",
                    interval=self.config.hot_reload.interval,
                )
                await self._plugin_watcher.start()
        except (Exception, asyncio.CancelledError):
            self._running = False
            logger.exception("调度器/告警/热重载启动失败，回滚已启动资源")
            self._detach_alert_handler()
            try:
                await self.scheduler.shutdown(wait=False)
            except (Exception, asyncio.CancelledError):
                logger.exception("回滚调度器失败")
            try:
                await self._stop_plugin_watcher()
            except (Exception, asyncio.CancelledError):
                logger.exception("回滚插件热重载失败")
            try:
                await self.connection.stop()
            except (Exception, asyncio.CancelledError):
                logger.exception("回滚连接失败")
            try:
                await self.plugin_manager.shutdown()
            except (Exception, asyncio.CancelledError):
                logger.exception("清理插件失败")
            try:
                await self.db.close()
            except (Exception, asyncio.CancelledError):
                logger.exception("清理数据库失败")
            raise

        # ============ 生命周期钩子分发 ============

        # Bot 启动完成，所有插件加载完后调用 on_startup
        try:
            await self.plugin_manager.dispatch_lifecycle("on_startup")
        except (Exception, asyncio.CancelledError):
            pass  # dispatch_lifecycle already does exception isolation

        logger.info("Qingci-Bot CE 启动成功")

    async def stop(self) -> None:
        """停止 Bot"""
        if not self._started:
            # 从未调用过 start()，无需清理
            return
        logger.info("Qingci-Bot CE 停止中...")
        self._running = False

        # 先调用 on_shutdown 生命周期钩子，释放资源
        try:
            await self.plugin_manager.dispatch_lifecycle("on_shutdown")
        except (Exception, asyncio.CancelledError):
            pass  # exception isolation already inside

        # 先停止接收新事件
        try:
            await self.connection.stop()
        except (Exception, asyncio.CancelledError):
            logger.exception("OneBot 连接停止异常")

        # 等待进行中的事件处理完成（最多 5 秒）
        if self._pending_tasks:
            logger.info(f"等待 {len(self._pending_tasks)} 个事件处理完成...")
            done, pending = await asyncio.wait(self._pending_tasks, timeout=5)
            for task in pending:
                task.cancel()
            # 等待取消的任务完成，避免 "Task destroyed while pending" 警告
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._pending_tasks.clear()

        try:
            await self.plugin_manager.shutdown()
        except (Exception, asyncio.CancelledError):
            logger.exception("插件卸载异常")

        # 关闭定时任务调度器（wait=False 快速返回，配合 stop 的超时保护语义）
        try:
            await self.scheduler.shutdown(wait=False)
        except (Exception, asyncio.CancelledError):
            logger.exception("调度器关闭异常")

        # 卸载错误告警处理器
        try:
            self._detach_alert_handler()
        except (Exception, asyncio.CancelledError):
            logger.exception("告警处理器卸载异常")

        # 停止插件热重载监听器
        try:
            await self._stop_plugin_watcher()
        except (Exception, asyncio.CancelledError):
            logger.exception("插件热重载停止异常")

        # 等待后台任务（如用量统计、会话持久化）完成，避免 DB 关闭时写失败
        try:
            from .tasks import await_pending_tasks

            await await_pending_tasks(timeout=3)
        except (Exception, asyncio.CancelledError):
            logger.exception("等待后台任务完成异常")

        try:
            await self.llm.close()
        except (Exception, asyncio.CancelledError):
            logger.exception("LLM 关闭异常")

        try:
            await self.db.close()
        except (Exception, asyncio.CancelledError):
            logger.exception("数据库关闭异常")

        logger.info("Qingci-Bot CE 已停止")

    def _detach_alert_handler(self) -> None:
        """卸载错误告警处理器（幂等）"""
        if self._alert_handler is not None:
            self._alert_handler.detach()
            self._alert_handler = None

    async def _stop_plugin_watcher(self) -> None:
        """停止插件热重载监听器（幂等）"""
        if self._plugin_watcher is not None:
            await self._plugin_watcher.stop()
            self._plugin_watcher = None

    # ============ 生命周期钩子回调 ============

    async def _on_bot_connect(self) -> None:
        """LLBot 连接建立时（初始连接与重连）触发分发 on_bot_connect"""
        await self.plugin_manager.dispatch_lifecycle("on_bot_connect")

    async def _on_metaevent(self, event: dict) -> None:
        """元事件到达时触发分发 on_metaevent（heartbeat/lifecycle 等）"""
        await self.plugin_manager.dispatch_lifecycle("on_metaevent", event)

    # ============ 事件处理 ============

    def _spawn_background_task(self, coro, name: str = "") -> asyncio.Task:
        """创建后台任务并保存引用，防止任务被 GC 与异常静默丢失

        委托至公共工具 bot.core.tasks.spawn_background_task，
        保留方法签名与返回值语义（返回 asyncio.Task）。
        """
        return spawn_background_task(coro, name=name)

    # ============ 全局事件钩子（消息中间件）============

    def register_pre_hook(self, fn) -> None:
        """注册前置钩子：async (event, ctx) -> Optional[str]

        钩子返回非 None 时拦截该事件，返回值作为回复发送并终止分发
        （跳过 Matcher 与旧式回调）。注册自动去重。
        """
        if fn not in self._pre_event_hooks:
            self._pre_event_hooks.append(fn)

    def register_post_hook(self, fn) -> None:
        """注册后置钩子：async (event, ctx, reply) -> None

        在消息回复发送后触发（reply 为最终回复或 None），用于横切
        统计/审计。异常隔离，不影响主链路。注册自动去重。
        """
        if fn not in self._post_event_hooks:
            self._post_event_hooks.append(fn)

    def add_matcher_preprocessor(self, fn) -> None:
        """注册 Matcher 运行前钩子（run_preprocessor）

        在 Matcher 匹配成功、handler 运行前触发。签名：
            async (bot, matcher, mctx) -> Optional[str] | Optional[Any]
        返回非 None 时拦截该 Matcher，返回值作为回复并停止整个分发链
        （跳过后续 Matcher 与旧式回调）；返回 None 则放行。
        钩子可改写 mctx（如注入统计、改写字段）后放行。异常隔离，单个钩子
        异常不影响其他钩子与主链路。注册自动去重。
        """
        if fn not in self._matcher_preprocessors:
            self._matcher_preprocessors.append(fn)

    def register_api_hook(self, fn) -> None:
        """注册平台接口调用钩子（on_calling_api）

        每次 Bot 调用 OneBot API 前触发。签名：
            async (api_name, params) -> Optional[dict]
        返回新 params 时替换原参数；返回 None 保持原样；抛异常则阻止该次
        API 调用。用于横切鉴权、参数改写、审计。注册自动去重。
        """
        self.connection.on_api_call(fn)

    async def _run_post_hooks(self, event: dict, ctx: MessageContext, reply: str | None) -> None:
        """执行后置钩子（异常隔离）"""
        for hook in self._post_event_hooks:
            try:
                res = hook(event, ctx, reply)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                logger.exception("后置钩子执行异常")

    async def _handle_event(self, event: dict) -> None:
        """处理 OneBot 事件 - 创建独立任务避免 stop() 死锁

        待处理任务达到上限时丢弃新事件，防止洪峰下任务无界堆积耗尽内存。
        """
        if not self._running:
            return
        if len(self._pending_tasks) >= _MAX_PENDING_EVENTS:
            logger.warning(f"事件积压过多（{len(self._pending_tasks)} pending），丢弃新事件")
            return
        task = self._spawn_background_task(self._process_event(event), "event-process")
        self._pending_tasks.add(task)

        def _cleanup(t: asyncio.Task[Any]) -> None:
            self._pending_tasks.discard(t)

        task.add_done_callback(_cleanup)

    async def _process_event(self, event: dict) -> None:
        """实际事件处理逻辑（受并发信号量限流，洪峰时排队等待）"""
        async with self._event_sem:
            await self._process_event_impl(event)

    async def _process_event_impl(self, event: dict) -> None:
        """事件处理实现（限流后执行）"""
        try:
            ctx = self.dispatcher.dispatch(event)
            if ctx is None:  # 防御性检查，dispatch 当前总返回非 None
                return

            post_type = ctx.post_type or event.get("post_type", "")

            # 前置钩子：可在分发前拦截事件（返回非 None 即作为回复发送并终止）
            for hook in self._pre_event_hooks:
                try:
                    res = hook(event, ctx)
                    if hasattr(res, "__await__"):
                        res = await res
                    if res:
                        await self._send_reply(ctx, str(res))
                        return
                except Exception:
                    logger.exception("前置钩子执行异常")

            if post_type != "message":
                matcher_reply, matcher_blocked = await self.dispatcher._run_event_matchers(
                    self, event, ctx
                )
                if matcher_reply is not None:
                    # request Matcher 返回 bool 表示审批结果（True 同意 / False 拒绝）。
                    # 与旧式 on_request 的审批语义对齐：非空结果即执行审批。
                    if post_type == "request" and isinstance(matcher_reply, (bool, int)):
                        await self._handle_request_approval(event, bool(matcher_reply))
                    return
                if matcher_blocked:
                    # Matcher 已匹配但未返回结果，跳过旧式回调
                    return
                # 旧式回调 fallback（仅跳过注册了同类型事件 Matcher 的插件，
                # 而非任一 Matcher——消息 Matcher 不应禁用其 notice/request 回调）
                for plugin in list(self.plugin_manager.plugins.values()):
                    if plugin.status != PluginStatus.LOADED:
                        continue
                    if self._plugin_has_event_matcher(plugin, post_type):
                        continue
                    try:
                        if post_type == "notice":
                            await plugin.on_notice(event)
                        elif post_type == "request":
                            approve = await plugin.on_request(event)
                            if approve is not None:
                                await self._handle_request_approval(event, approve)
                                break  # request 已审批，跳出循环
                    except Exception:
                        logger.exception(
                            f"插件处理异常: {plugin.name}, "
                            f"post_type={post_type}, "
                            f"event_summary={self._event_summary(event)}"
                        )
                return

            reply, blocked = await self.dispatcher.run_matchers(self, event, ctx)
            if reply is not None:
                await self._send_reply(ctx, reply)
                await self._run_post_hooks(event, ctx, reply)
                return
            if blocked:
                # block 语义：Matcher 已消费该事件（handler 未返回回复），阻止旧式回调
                return

            for plugin in list(self.plugin_manager.plugins.values()):
                if plugin.status != PluginStatus.LOADED:
                    continue
                if self._plugin_has_event_matcher(plugin, "message"):
                    continue
                try:
                    reply = await plugin.on_message(ctx)
                    if reply:
                        await self._send_reply(ctx, reply)
                        await self._run_post_hooks(event, ctx, reply)
                        break
                except Exception:
                    logger.exception(
                        f"插件处理异常: {plugin.name}, "
                        f"post_type={post_type}, "
                        f"event_summary={self._event_summary(event)}"
                    )
        except Exception:
            logger.exception(f"处理事件异常: {event.get('post_type', 'unknown')}")

    @staticmethod
    def _plugin_has_event_matcher(plugin, post_type: str) -> bool:
        """插件是否注册了指定事件类型的 Matcher（用于决定是否走旧式回调）"""
        for m in getattr(plugin, "matchers", None) or []:
            if getattr(m, "event_type", "message") == post_type:
                return True
        return False

    @staticmethod
    def _event_summary(event: dict) -> str:
        """生成事件摘要（用于日志）"""
        return (
            f"user_id={event.get('user_id')}, "
            f"group_id={event.get('group_id')}, "
            f"message_type={event.get('message_type')}, "
            f"request_type={event.get('request_type')}, "
            f"notice_type={event.get('notice_type')}"
        )

    async def _send_reply(self, ctx: MessageContext, reply: str) -> None:
        """发送插件回复"""
        target_id = ctx.group_id if ctx.message_type == "group" else ctx.user_id
        if not target_id:
            logger.warning(
                f"无法发送回复：target_id 为空 (type={ctx.message_type}, "
                f"user_id={ctx.user_id}, group_id={ctx.group_id})"
            )
            return
        if ctx.message_type == "group" and ctx.user_id:
            prefix = ""
            if ctx.message_id:
                prefix += MessageDispatcher.build_cq_reply(ctx.message_id)
            prefix += MessageDispatcher.build_cq_at(ctx.user_id)
            reply = prefix + " " + reply

        for attempt in range(3):
            try:
                await self.connection.send_msg(ctx.message_type, target_id, reply)
                return
            except Exception:
                logger.exception(
                    f"发送消息失败 (attempt {attempt + 1}/3, "
                    f"type={ctx.message_type}, target={target_id})"
                )
                if attempt < 2:
                    await asyncio.sleep(0.5)

    async def _handle_request_approval(self, event: dict, approve: bool) -> None:
        """处理加好友/加群请求的审批结果"""
        try:
            request_type = event.get("request_type", "")
            flag = event.get("flag", "")
            if not flag:
                return
            if request_type == "friend":
                await self.connection.call_api(
                    "set_friend_add_request", {"flag": flag, "approve": approve}
                )
            elif request_type == "group":
                sub_type = event.get("sub_type", "")
                await self.connection.call_api(
                    "set_group_add_request",
                    {"flag": flag, "sub_type": sub_type, "approve": approve},
                )
        except Exception:
            logger.exception("处理请求审批失败")

    # ============ 状态 ============

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        """获取 Bot 状态"""
        return {
            "running": self._running,
            "connected": self.connection.is_connected,
            "last_heartbeat": self.connection.last_heartbeat,
            "plugins": [
                {
                    "name": p.name,
                    "version": p.version,
                    "description": p.description,
                    "category": p.category,
                    "status": p.status.value,
                    "enabled": p.enabled,
                }
                for p in self.plugin_manager.plugins.values()
            ],
        }


# ============ 全局实例 ============

_bot_instance: QingciBot | None = None


def get_bot() -> QingciBot:
    """获取全局 Bot 实例"""
    if _bot_instance is None:
        raise RuntimeError("Bot 未初始化")
    return _bot_instance


def set_bot(bot: QingciBot):
    global _bot_instance
    _bot_instance = bot


def clear_bot():
    global _bot_instance
    _bot_instance = None
