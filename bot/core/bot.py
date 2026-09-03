"""Bot 主类 - 生命周期管理、组件编排"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

# v12 消息段工厂（发送回复前缀组装用）
from qingci_plugin_sdk.segments import MessageSegment

from .. import __version__
from ..alerter import AlertHandler
from ..config import ConfigManager
from ..paths import plugins_dir
from ..plugin.protocol.base import PluginStatus
from ..plugin.protocol.context import MessageContext
from ..plugin.protocol.events import parse_event
from ..plugin.watcher import PluginWatcher
from .di import DIContainer
from .dispatcher import MessageDispatcher
from .tasks import spawn_background_task

if TYPE_CHECKING:
    from ..db import Database
    from ..filter import SensitiveFilter
    from ..html_renderer import HtmlRenderer
    from ..llm import EventBuffer, LLMManager, ToolRegistry
    from ..plugin import PluginManager
    from ..plugin.protocol.ratelimit import RateLimiter
    from ..rag import KnowledgeStore
    from .event_bus import EventBus
    from .platforms.base import PlatformAdapter
    from .platforms.onebot11 import OneBotConnection
    from .scheduler import BotScheduler
    from .session_state import SessionStateManager

logger = logging.getLogger("qingci-bot")

# 事件处理并发上限：防止消息洪峰时并发任务无界堆积（超限任务排队等待）
_EVENT_CONCURRENCY = 16
# 事件排队上限：_handle_event 中待处理（排队+执行）任务超过该值时丢弃新事件，
# 防止洪峰时任务无界堆积导致内存耗尽（此时丢弃比拖垮整个进程更可接受）
_MAX_PENDING_EVENTS = 128


def _safe_int(value, default: int = 0) -> int:
    """安全转 int：非数值（字符串/None/非法）回退默认，避免状态接口 500"""
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


class QingciBot:
    """Qingci-Bot CE 主类"""

    # ---- 组件属性（由 composition.assemble_bot 创建并注入，供类型检查）----
    config: ConfigManager
    db: "Database"
    connection: "OneBotConnection"
    dispatcher: MessageDispatcher
    llm: "LLMManager"
    plugin_manager: "PluginManager"
    di: DIContainer
    session_state: "SessionStateManager"
    event_bus: "EventBus"
    rate_limiter: "RateLimiter | None"
    scheduler: "BotScheduler"
    tool_registry: "ToolRegistry"
    # 类型化事件缓冲（composition 创建，供 LLM 事件查询工具读取）
    event_buffer: "EventBuffer | None"
    # 平台适配器表（composition 创建：onebot 为默认，其余按配置启用）
    platforms: dict[str, "PlatformAdapter"]
    knowledge_store: "KnowledgeStore | None"
    sensitive_filter: "SensitiveFilter"
    # HTML → 图片渲染服务（可选能力；playwright 未安装时不可用，不影响启动）
    html_renderer: "HtmlRenderer"
    # 插件热重载监听器（composition 初始化为 None，start 中按需创建）
    _plugin_watcher: "PluginWatcher | None"

    def __init__(self, config_path: str | None = None):
        path = Path(config_path) if config_path else None
        self.config = ConfigManager(path)
        self.config.load()

        # 组件装配（核心服务创建 + DI 注册）收敛到组合根，
        # 保持 __init__ 只做配置加载与状态字段初始化
        from .composition import assemble_bot

        assemble_bot(self)

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

    # ============ 生命周期 ============

    async def start(self) -> None:
        """启动 Bot"""
        self._started = True
        logger.info("Qingci-Bot CE 启动中...")
        await self.db.connect()
        # 会话状态持久化：开启时在首个事件到达前从快照恢复（幂等，缺失即空启动）
        if self.session_state is not None and self.config.session_state.enabled:
            try:
                from ..core.session_persistence import restore_snapshot

                await restore_snapshot(self.session_state)
                logger.info("会话状态快照已恢复")
            except Exception:
                logger.exception("恢复会话状态快照失败（继续空启动）")
        await self.plugin_manager.load_builtin(self)
        # 加载外部插件目录（plugins_dir()：默认 app_root/plugins/，实例模式下为该实例 plugins/）
        await self.plugin_manager.load_external_dir(self)
        # 应用全局语言到插件 i18n
        self.plugin_manager.set_i18n_locale(self.config.config.lang)
        # 初始化 MCP 工具（enable_tools + mcp_servers 配置时；失败仅记日志）
        await self.llm.setup_mcp_tools()
        # 多平台：onebot 为主连接，其余按 platforms 配置启用的适配器一并启动
        for platform in self.platforms.values():
            platform.on_event(self._handle_event)
            platform.on_connect(self._on_bot_connect)
            platform.on_metaevent(self._on_metaevent)
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

        # 启动附加平台适配器（Telegram 等；失败仅记日志，不阻断主平台）
        for name, platform in self.platforms.items():
            if name == "onebot" or platform is self.connection:
                continue
            try:
                await platform.start()
                logger.info(f"平台适配器已启动: {platform.display_name}")
            except Exception:
                logger.exception(f"平台适配器启动失败: {name}（该平台将不可用）")

        # 后台探测 HTML 渲染能力（失败仅记日志，结果供 /api/bot/status 与插件查询）
        self._spawn_background_task(self.html_renderer.probe(), name="html-render-probe")

        # 定期清理限流器过期条目（RateLimiter._data 无自动淘汰，防内存缓慢增长）
        if self.rate_limiter is not None:
            self._spawn_background_task(
                self._rate_limiter_cleanup_loop(), name="rate-limiter-cleanup"
            )

        # 会话状态周期快照（防中途崩溃丢太多；随 stop() 置 _running=False 退出）
        if self.config.session_state.enabled:
            self._spawn_background_task(
                self._session_snapshot_loop(), name="session-state-snapshot"
            )

        # 定期清理过期数据（messages/usage_logs/audit_logs/sessions 保留期，
        # 防长期运行单表无限膨胀；retention_days<=0 时不启动）
        if getattr(self.config.log, "retention_days", 0) > 0:
            self._spawn_background_task(self._data_retention_loop(), name="data-retention-cleanup")

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
                    directory=plugins_dir(),
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

        # 停止附加平台适配器（异常隔离）
        for name, platform in self.platforms.items():
            if name == "onebot" or platform is self.connection:
                continue
            try:
                await platform.stop()
            except (Exception, asyncio.CancelledError):
                logger.exception(f"平台适配器停止异常: {name}")

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

        # 会话状态持久化：最终落盘（_running=False 已使周期快照循环退出，此处唯一最终保存点）
        if self.session_state is not None and self.config.session_state.enabled:
            try:
                from ..core.session_persistence import save_snapshot

                await save_snapshot(self.session_state)
                logger.info("会话状态快照已保存")
            except Exception:
                logger.exception("保存会话状态快照失败")

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

        # 关闭 HTML 渲染服务（幂等；未使用过渲染器时零开销）
        try:
            await self.html_renderer.close()
        except (Exception, asyncio.CancelledError):
            logger.exception("HTML 渲染器关闭异常")

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
        """协议端连接建立时（初始连接与重连）触发分发 on_bot_connect"""
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

        每次 Bot 调用平台 API 前触发（所有已启用平台适配器生效）。签名：
            async (api_name, params) -> Optional[dict]
        返回新 params 时替换原参数；返回 None 保持原样；抛异常则阻止该次
        API 调用。用于横切鉴权、参数改写、审计。注册自动去重。
        """
        self.connection.on_api_call(fn)
        for platform in self.platforms.values():
            platform.on_api_call(fn)

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
            # 事件链路追踪：把本事件的 id 注入日志上下文，跨模块串查
            # 分发/匹配/插件/LLM 处理日志（v12 事件带 id；v11 用 message_id/flag）
            from ..logformat import set_event_id

            set_event_id(str(event.get("id") or event.get("message_id") or event.get("flag") or ""))
            ctx = self.dispatcher.dispatch(event)
            if ctx is None:  # 防御性检查，dispatch 当前总返回非 None
                return

            post_type = ctx.post_type or event.get("post_type", "")

            # 类型化事件入缓冲：notice/request 事件写入 EventBuffer
            # （无论是否有 Matcher 都记录，供 LLM 事件查询工具读取）
            if post_type in ("notice", "request"):
                buffer = getattr(self, "event_buffer", None)
                if buffer is not None:
                    try:
                        buffer.record(parse_event(post_type, event) or event)
                    except Exception:
                        logger.debug("事件入缓冲失败，忽略", exc_info=True)

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
                    # 字符串回复则作为审批回复消息发送（不再静默吞掉）。
                    if post_type == "request" and isinstance(matcher_reply, (bool, int)):
                        await self._handle_request_approval(
                            event, bool(matcher_reply), getattr(ctx, "platform", "")
                        )
                    elif isinstance(matcher_reply, str):
                        await self._send_reply(ctx, matcher_reply)
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
                                await self._handle_request_approval(
                                    event, approve, getattr(ctx, "platform", "")
                                )
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

    async def _send_reply(self, ctx: MessageContext, reply: str | list) -> bool:
        """发送插件回复（按 ctx.platform 路由到对应平台适配器）

        OneBot 12 迁移（方案 A）：群聊回复前缀不再拼 CQ 码字符串，
        改为组装 v12 段数组 [reply][mention][text]，由平台适配器负责
        序列化（v11 平台转 CQ，v12 平台原样透传）。

        Returns:
            是否发送成功（三次重试均失败返回 False，供调用方感知“必须送达”的语义消息）
        """
        target_id = ctx.group_id if ctx.message_type == "group" else ctx.user_id
        if not target_id:
            logger.warning(
                f"无法发送回复：target_id 为空 (type={ctx.message_type}, "
                f"user_id={ctx.user_id}, group_id={ctx.group_id})"
            )
            return False

        message: str | list = reply
        if ctx.message_type == "group" and ctx.user_id:
            segments: list = []
            if ctx.message_id:
                segments.append(MessageSegment.reply(str(ctx.message_id)))
            segments.append(MessageSegment.mention(str(ctx.user_id)))
            if isinstance(reply, str):
                segments.append(MessageSegment.text(reply))
            elif isinstance(reply, list):
                # handler 返回的 v12 段数组（图片等多媒体）：拼在回复/提及之后
                segments.extend(reply)
            message = segments

        # 按事件来源平台路由；未知平台回退主连接
        platform = getattr(ctx, "platform", "") or ""
        conn = self.platforms.get(platform, self.connection)

        for attempt in range(3):
            try:
                await conn.send_msg(ctx.message_type, target_id, message)
                return True
            except Exception:
                logger.exception(
                    f"发送消息失败 (attempt {attempt + 1}/3, "
                    f"platform={platform or 'onebot'}, "
                    f"type={ctx.message_type}, target={target_id})"
                )
                if attempt < 2:
                    await asyncio.sleep(0.5)
        logger.error(
            f"发送消息失败（已重试 3 次，视为未送达）: platform={platform or 'onebot'}, "
            f"type={ctx.message_type}, target={target_id}"
        )
        return False

    async def _rate_limiter_cleanup_loop(self) -> None:
        """周期清理限流器过期条目（每小时；随 stop() 置 _running=False 退出）"""
        while self._running:
            await asyncio.sleep(3600)
            try:
                if self.rate_limiter is not None:
                    self.rate_limiter.cleanup()
            except Exception:
                logger.exception("清理限流器过期条目失败")

    async def _session_snapshot_loop(self) -> None:
        """周期保存会话状态快照（按 session_state.snapshot_interval；随 stop() 退出）"""
        interval = max(float(self.config.session_state.snapshot_interval), 10.0)
        while self._running:
            await asyncio.sleep(interval)
            try:
                from ..core.session_persistence import save_snapshot

                await save_snapshot(self.session_state)
            except Exception:
                logger.exception("周期保存会话状态快照失败")

    async def _data_retention_loop(self) -> None:
        """定期清理超过保留期的历史数据（每天；随 stop() 置 _running=False 退出）

        覆盖表：messages / sessions / usage_logs / audit_logs。仅当
        config.log.retention_days > 0 时由 start() 启动；单次清理失败
        仅记日志，不影响 Bot 主流程（下次循环重试）。
        """
        while self._running:
            try:
                retention = getattr(self.config.log, "retention_days", 0)
                if retention > 0:
                    await self._purge_expired_data(retention)
            except Exception:
                logger.exception("清理过期数据失败")
            # 每天一次（首次启动后 24h 触发；睡眠分片避免 stop() 等待过久）
            slept = 0.0
            while self._running and slept < 86400:
                await asyncio.sleep(60)
                slept += 60

    async def _purge_expired_data(self, retention_days: int) -> None:
        """删除超过保留期的消息/会话/用量/审计记录（按 created_at 批量）"""
        import datetime

        from sqlalchemy import delete

        from ..db.engine import session_scope
        from ..db.models import AuditLog, Message, SessionHistory, UsageLog

        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=retention_days
        )
        tables = (Message, SessionHistory, UsageLog, AuditLog)
        total = 0
        async with session_scope() as session:
            for model in tables:
                # 各模型均含 created_at 列；rowcount 在 CursorResult 上，SQLAlchemy
                # 类型为 Result 基类时用 getattr 兜底（运行时行为一致）
                result = await session.execute(
                    delete(model).where(model.created_at < cutoff)  # type: ignore[arg-type,union-attr]
                )
                total += getattr(result, "rowcount", 0) or 0
        if total:
            logger.info(f"数据保留清理完成：删除 {total} 条超过 {retention_days} 天的记录")

    async def _handle_request_approval(
        self, event: dict, approve: bool, platform: str = ""
    ) -> None:
        """处理加好友/加群请求的审批结果

        v11 事件的 request_type（friend/group）在适配器翻译为 v12 事件时
        已映射为 detail_type，故此处优先读取 detail_type；同时兼容未翻译的
        v11 形态（request_type），避免此类事件审批静默失效。

        按能力面路由：supports_request_approval=True 的平台调用其
        approve_request 映射到自身 action；不支持的平台记录告警并跳过
        （不再按平台名硬编码 action，新增平台无需改核心代码）。
        """
        try:
            request_type = event.get("detail_type", "") or event.get("request_type", "")
            flag = event.get("flag", "")
            if not flag:
                return
            if not request_type:
                logger.warning(
                    f"请求事件缺少 detail_type/request_type，跳过审批: {self._event_summary(event)}"
                )
                return
            # 按事件来源平台路由连接；未知平台回退主连接
            conn = self.platforms.get(platform, self.connection)
            if not getattr(conn, "supports_request_approval", False):
                logger.warning(f"平台 {getattr(conn, 'name', platform)} 不支持请求审批，已跳过")
                return
            sub_type = event.get("sub_type", "")
            try:
                await conn.approve_request(
                    flag, approve, request_type=request_type, sub_type=sub_type
                )
            except NotImplementedError:
                logger.warning(f"平台 {getattr(conn, 'name', platform)} 未实现请求审批，已跳过")
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
            "version": __version__,
            "last_heartbeat": self.connection.last_heartbeat,
            "render": self.html_renderer.status_info(),
            "platforms": [
                {
                    "name": p.name,
                    "display_name": p.display_name,
                    "connected": bool(p.is_connected),
                    "last_heartbeat": p.last_heartbeat,
                    "self_id": _safe_int(getattr(p, "self_id", 0)),
                    **p.status_info(),
                }
                for p in self.platforms.values()
                # 仅上报已启用的适配器：如 Telegram 主平台实例下 OneBot 已停用
                # （enabled=False），不应出现在平台状态列表中
                if getattr(p, "enabled", True)
            ],
            "plugins": [
                {
                    "name": p.name,
                    "version": p.version,
                    "description": p.description,
                    "category": p.category,
                    "status": p.status.value,
                    "enabled": p.enabled,
                    # 供 WebUI 渲染插件卡片上的 Web 管理页面入口按钮
                    "pages": self.plugin_manager.get_plugin_pages(p.name),
                }
                for p in self.plugin_manager.plugins.values()
            ],
        }


# ============ 全局实例访问 ============

# 当前进程的 DI 容器引用（set_bot 时注入）。与旧式"模块级 bot 单例"不同，
# 这里只持有容器，bot 实例通过容器解析（QingciBot 在 __init__ 中已
# register_sync(QingciBot, self)），避免 bot 实例本身成为模块级状态。
_bot_container: DIContainer | None = None


def get_bot() -> QingciBot:
    """获取当前进程的 Bot 实例（从 DI 容器解析）"""
    if _bot_container is None:
        raise RuntimeError("Bot 未初始化")
    bot = _bot_container.resolve_sync(QingciBot)
    if bot is None:
        raise RuntimeError("Bot 未初始化")
    return cast(QingciBot, bot)


def set_bot(bot: QingciBot):
    """记录当前进程的 DI 容器（供 get_bot 解析）

    架构约束：单进程 = 单 Bot 实例。重复设置说明已有实例存活
    （异常多开/测试未清理），记录告警便于排查；不主动抛错，
    以免打断测试重置等合法场景。
    """
    global _bot_container
    if _bot_container is not None and _bot_container is not bot.di:
        logger.warning(
            "set_bot 被重复调用：已有 Bot 实例注册，现被新实例覆盖（单进程应只存在一个 Bot 实例）"
        )
    _bot_container = bot.di


def clear_bot():
    """清空当前进程的 DI 容器引用"""
    global _bot_container
    _bot_container = None
