"""消息分发器 - 事件路由、消息预处理、Matcher 调度

双轨调度：
1. 新式 Matcher：按 priority 排序，检查 rule + permission，匹配则执行 handler
2. 旧式 on_message：Matcher 全部未匹配后，依次调用插件的 on_message

Matcher handler 返回非 None（回复文本）则停止整个分发链。
block=True 的 Matcher 匹配后（无论 handler 返回什么）停止后续 Matcher。
已注册 Matcher 的插件不再走旧式 on_message 调度。
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

# 消息上下文统一由独立插件 SDK 定义（qingci_plugin_sdk.context.MessageContext），
# 主项目与外部插件共用同一类型，避免协议层定义漂移。
from qingci_plugin_sdk.segments import Message

from ..plugin.protocol.context import MessageContext
from ..plugin.protocol.events import parse_event
from ..plugin.protocol.matcher import MatcherContext
from ..plugin.protocol.session import (
    FinishException,
    PauseException,
    RejectException,
    Session,
)

if TYPE_CHECKING:
    from .bot import QingciBot

logger = logging.getLogger("qingci-bot.dispatcher")


class _PendingStep:
    """等待中的会话阶梯：续接同一 matcher 直到 finish

    session 实例跨轮复用（保留 handler 挂载的自定义属性），
    expire_at 为挂起超时（monotonic 时间戳）。
    """

    __slots__ = ("matcher", "plugin", "session", "expire_at")

    def __init__(self, matcher, plugin, session: Session, ttl: float):
        self.matcher = matcher
        self.plugin = plugin
        self.session = session
        self.expire_at = time.monotonic() + ttl


def _to_reply(result: Any, post_type: str) -> Any:
    """将 Matcher 返回值转为回复：消息事件 str 化，事件事件保留原始类型

    事件事件保留原始类型的目的是让 request Matcher 返回的 bool（审批结果）
    可被上层正确识别。
    """
    return str(result) if post_type == "message" else result


class MessageDispatcher:
    """消息分发器：解析事件、路由到插件"""

    def __init__(self):
        # 会话阶梯：会话键 -> 等待中的 _PendingStep
        self._pending_steps: dict[str, _PendingStep] = {}
        self._steps_lock = asyncio.Lock()
        # 阶梯挂起默认超时（秒）：超时后下一条同会话消息不再续接
        self.step_ttl: float = 300.0

    def dispatch(self, event: dict) -> MessageContext:
        """分发事件（仅解析，不执行 Matcher）

        Matcher 调度由 PluginManager + Dispatcher.run_matchers 完成。
        对 notice/request 等非消息事件也填充基础字段，使事件 Matcher 的
        权限/规则检查（如按 user_id/group_id 过滤）可用。

        OneBot 12 迁移（方案 A）：双模事件输入。
        - OneBot 12 事件（含 type 字段）→ MessageContext.from_v12_event
          （post_type/message_type 由 type/detail_type 派生）
        - OneBot 11 事件（post_type 字段，来自 aiocqhttp/telegram 旧适配器）
          → 本模块的 v11 解析路径（M3 平台适配器迁移完成后此路径移除）
        """
        # v12 事件：type 为必填基础字段
        if event.get("type"):
            return MessageContext.from_v12_event(event)

        post_type = event.get("post_type", "")

        ctx = MessageContext(raw_event=event)
        if post_type == "message":
            ctx = self._parse_message(event)
        else:
            ctx.post_type = post_type
            ctx.sub_type = event.get("sub_type", "")
            ctx.self_id = self._safe_int(event.get("self_id"))
            ctx.user_id = self._safe_int(event.get("user_id"))
            ctx.group_id = self._safe_int(event.get("group_id"))
            ctx.sender = event.get("sender", {}) or {}

        # 事件来源平台（适配器在事件 dict 中注入 platform，默认 onebot）
        ctx.platform = str(event.get("platform", "onebot") or "onebot")

        return ctx

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        """安全地将值转为 int，非法值返回 default（避免异常吞掉整个事件）"""
        try:
            return int(value or 0)
        except (ValueError, TypeError):
            return default

    async def run_matchers(
        self, bot: "QingciBot", event: dict, ctx: MessageContext
    ) -> tuple[str | None, bool]:
        """执行消息 Matcher 调度（包装 _run_matchers，保持调用方兼容）"""
        return await self._run_matchers(bot, event, ctx, "message")

    async def _run_event_matchers(
        self, bot: "QingciBot", event: dict, ctx: MessageContext
    ) -> tuple[str | None, bool]:
        """执行 notice/request 事件 Matcher 调度（包装 _run_matchers，保持调用方兼容）

        OneBot 12 迁移：v12 事件无 post_type 字段，事件类型以
        ctx.post_type（已由 type 派生）为准。
        """
        post_type = ctx.post_type or event.get("post_type", "")
        return await self._run_matchers(bot, event, ctx, post_type)

    async def _run_matchers(
        self, bot: "QingciBot", event: dict, ctx: MessageContext, post_type: str
    ) -> tuple[str | None, bool]:
        """统一 Matcher 调度

        按事件类型预过滤，逐个检查 permission + rule，匹配则执行 handler。

        返回 (reply, blocked)：
        - reply: handler 返回的回复。消息事件 str 化为文本；事件事件保留
          原始类型，使 request Matcher 的 bool 审批结果可被上层正确识别。
        - blocked: 是否发生 block 语义（匹配成功且 block=True，或已有回复），
          此时应停止整个分发链（含旧式回调）。
        """
        # 重载屏障读锁：与插件热重载的写锁互斥——重载期间等待分发结束，
        # 分发期间阻塞重载，避免读到半新半旧的 Matcher 注册表/模块状态。
        rw = getattr(bot.plugin_manager, "_reload_rw", None)
        if rw is not None:
            await rw.acquire_read()
        try:
            # 会话阶梯续接优先：若该会话存在挂起中的阶梯，跳过 rule/permission
            # 直接续接 handler（用户已进入多轮流程，不应被命令前缀规则再次拦截）
            if post_type == "message":
                step_reply, step_blocked = await self._try_resume_step(bot, ctx)
                if step_blocked:
                    return step_reply, True

            # 事件类型倒排索引：直接取该事件类型的 Matcher，避免对全部
            # Matcher 线性扫描过滤（消息事件不再遍历 notice/request Matcher）
            event_matchers = bot.plugin_manager.all_matchers(post_type)
            if not event_matchers:
                return None, False

            # 会话状态每事件预取一次，供所有 Matcher 复用（避免逐 Matcher 重复查询）
            session_state = None
            if bot.session_state is not None:
                session_state = await bot.session_state.get_session(
                    user_id=ctx.user_id,
                    group_id=ctx.group_id,
                    message_type=ctx.message_type,
                )

            for matcher in event_matchers:
                mctx = MatcherContext.from_message_context(
                    ctx, bot=bot, plugin=None, matcher=matcher
                )
                mctx.session_state = session_state
                # 注入会话阶梯对象：handler 可调用 ctx.session.pause/finish 等控制多轮流程
                mctx.session = Session(
                    send_fn=lambda text: self._send_text(bot, ctx, text),
                    step_key=self._step_key(ctx),
                    step_ttl=self.step_ttl,
                )
                # 类型化事件：notice/request 事件解析为类型化对象（handler 按注解注入）
                if post_type in ("notice", "request") and mctx.event is None:
                    mctx.event = parse_event(post_type, event)
                if matcher.owner:
                    plugin = bot.plugin_manager.get(matcher.owner)
                    if plugin is None:
                        continue
                    mctx.plugin = plugin

                try:
                    if not await matcher.permission.check(bot, event, mctx):
                        continue
                    if not await matcher.rule.check(bot, event, mctx):
                        continue

                    mctx.matcher = matcher

                    # Matcher 运行前全局钩子（run_preprocessor）：拦截则停止分发
                    if bot._matcher_preprocessors:
                        intercepted = await self._run_preprocessors(bot, matcher, mctx)
                        if intercepted is not None:
                            return _to_reply(intercepted, post_type), True

                    # 执行 handler（含指标 + 中间件）
                    try:
                        result = await self._execute_handler(bot, matcher, mctx)
                    except PauseException:
                        # 挂起：等待同会话下一条消息续接同一 handler
                        await self._register_step(bot, matcher, mctx.plugin, mctx)
                        return None, True
                    except FinishException:
                        # 结束：清除阶梯，本消息已由 session 内部发送
                        await self._clear_step(ctx)
                        return None, True
                    except RejectException:
                        # 拒绝：保留阶梯继续等待（重新计时）
                        await self._register_step(bot, matcher, mctx.plugin, mctx)
                        return None, True
                    finally:
                        # temp 一次性匹配器：无论 handler 成功/异常都移除，
                        # 避免失败后反复匹配刷错
                        if matcher.temp:
                            bot.plugin_manager.remove_temp_matcher(matcher)

                    if result is not None:
                        return _to_reply(result, post_type), True

                    if matcher.block:
                        return None, True

                except Exception:
                    logger.exception(
                        f"Matcher 执行异常: owner={matcher.owner}, "
                        f"handler={getattr(matcher.handler, '__name__', repr(matcher.handler))}"
                    )
                    continue

            return None, False
        finally:
            if rw is not None:
                await rw.release_read()

    # ---- 会话阶梯（多轮交互） ----

    @staticmethod
    def _step_key(ctx: MessageContext) -> str:
        """构建会话阶梯键：private:{uid} / group:{gid}:{uid}（与 SessionStateManager 一致）"""
        if ctx.message_type == "group" and ctx.group_id:
            return f"group:{ctx.group_id}:{ctx.user_id}"
        return f"private:{ctx.user_id}"

    async def _try_resume_step(self, bot: "QingciBot", ctx: MessageContext) -> tuple[Any, bool]:
        """尝试续接挂起中的会话阶梯

        命中则执行 handler（跳过 rule/permission），并按其控制流更新阶梯状态。

        返回 (reply, blocked)：
        - blocked=True 表示已消费事件（含阶梯结束返回的回复）
        - blocked=False 表示无阶梯或已失效，走正常分发
        """
        key = self._step_key(ctx)
        async with self._steps_lock:
            step = self._pending_steps.get(key)
            if step is None:
                return None, False
            if time.monotonic() > step.expire_at:
                del self._pending_steps[key]
                logger.debug(f"会话阶梯超时清除: {key}")
                return None, False
            # 插件失效（卸载/重载）则丢弃阶梯
            plugin = bot.plugin_manager.get(step.matcher.owner) if step.matcher.owner else None
            if plugin is None or plugin is not step.plugin:
                del self._pending_steps[key]
                return None, False
            matcher = step.matcher
            session = step.session
            # 取出阶梯：handler 若再次 pause/reject 会重新注册，否则自然结束
            del self._pending_steps[key]

        mctx = MatcherContext.from_message_context(ctx, bot=bot, plugin=plugin, matcher=matcher)
        mctx.session_state = await self._get_session_state(bot, ctx)
        # 复用跨轮 Session 实例（保留 handler 挂载的自定义状态），仅重绑发送函数
        session._rebind_send(lambda text: self._send_text(bot, ctx, text))
        mctx.session = session

        try:
            result = await self._execute_handler(bot, matcher, mctx)
        except PauseException:
            await self._register_step(bot, matcher, plugin, mctx)
            return None, True
        except FinishException:
            return None, True
        except RejectException:
            await self._register_step(bot, matcher, plugin, mctx)
            return None, True
        except Exception:
            # 普通异常：记录日志并消费事件。阶梯已在上方删除，不残留悬挂状态；
            # 返回 (None, True) 保证不越过 _run_matchers 中断整个事件分发。
            logger.exception(
                f"会话阶梯续接异常: owner={matcher.owner}, "
                f"handler={getattr(matcher.handler, '__name__', repr(matcher.handler))}"
            )
            return None, True
        finally:
            if matcher.temp:
                bot.plugin_manager.remove_temp_matcher(matcher)

        # handler 正常返回：回复由上层发送，阶梯已结束（未 pause/reject 则不再续接）
        return result, True

    async def _register_step(
        self,
        bot: "QingciBot",
        matcher,
        plugin,
        mctx: "MatcherContext",
    ) -> None:
        """注册/刷新会话阶梯：等待同会话下一条消息续接同一 handler

        mctx.session 若不存在则新建（首次 pause）；存在则复用跨轮实例。
        """
        key = self._step_key(mctx)
        session = mctx.session
        if session is None:
            session = Session(
                send_fn=lambda text: self._send_text(bot, mctx, text),
                step_key=key,
                step_ttl=self.step_ttl,
            )
            mctx.session = session
        else:
            session._rebind_send(lambda text: self._send_text(bot, mctx, text))
        step = _PendingStep(matcher, plugin, session, ttl=self.step_ttl)
        async with self._steps_lock:
            # 惰性回收：顺带清除已过期的阶梯（用户 pause 后不再发言的驻留条目），
            # 避免 _pending_steps 长期占用内存/引用
            now = time.monotonic()
            stale = [k for k, s in self._pending_steps.items() if now > s.expire_at]
            for k in stale:
                del self._pending_steps[k]
            self._pending_steps[key] = step
        logger.debug(f"会话阶梯挂起: {key} -> matcher={matcher.owner}")

    async def _clear_step(self, ctx: MessageContext) -> None:
        """清除会话阶梯（finish / 插件停用等场景）"""
        key = self._step_key(ctx)
        async with self._steps_lock:
            self._pending_steps.pop(key, None)

    async def clear_steps_for(self, owner: str) -> None:
        """清除指定插件名下所有挂起阶梯（插件卸载/重载时调用）"""
        async with self._steps_lock:
            stale = [k for k, s in self._pending_steps.items() if s.matcher.owner == owner]
            for k in stale:
                del self._pending_steps[k]

    async def _get_session_state(self, bot: "QingciBot", ctx: MessageContext):
        """预取会话状态（所有 Matcher 复用，避免逐 Matcher 重复查询）"""
        if bot.session_state is None:
            return None
        return await bot.session_state.get_session(
            user_id=ctx.user_id,
            group_id=ctx.group_id,
            message_type=ctx.message_type,
        )

    async def _send_text(self, bot: "QingciBot", ctx: MessageContext, text: str) -> None:
        """Session 内部发送辅助：复用 bot 的回复通道"""
        from .bot import QingciBot  # noqa: F401

        await bot._send_reply(ctx, text)

    async def _run_preprocessors(self, bot: "QingciBot", matcher, mctx) -> Any:
        """执行 Matcher 运行前全局钩子（run_preprocessor）

        返回第一个钩子的非 None 拦截值；全部放行返回 None。单个钩子异常
        隔离（记录后继续下一个），不影响主链路。
        """
        for fn in list(bot._matcher_preprocessors):
            try:
                res = fn(bot, matcher, mctx)
                if hasattr(res, "__await__"):
                    res = await res
                if res is not None:
                    return res
            except Exception:
                logger.exception("Matcher 运行前钩子异常")
        return None

    async def _execute_handler(self, bot: "QingciBot", matcher, mctx) -> Any:
        """执行单个 Matcher handler（含指标记录 + 插件级中间件）

        返回 handler 的原始返回值（不 str 化），str 化由调用方按事件类型决定，
        使 request Matcher 的 bool 审批结果得以保留。
        """
        plugin = mctx.plugin
        start = time.perf_counter()
        is_error = False

        # 会话状态兜底：调用方通常已预取注入；仅当 bot.session_state 缺失时给空会话
        if mctx.session_state is None:
            from ..core.session_state import SessionState

            mctx.session_state = SessionState()

        try:
            # 插件级 before_handler 中间件
            if plugin is not None:
                for before_fn in plugin._before_handlers:
                    try:
                        intercept = before_fn(matcher, mctx)
                        if hasattr(intercept, "__await__"):
                            intercept = await intercept
                        if intercept is not None:
                            # 中间件拦截，返回拦截值作为回复
                            # 指标不在此记录：由下方 finally 统一记录一次，
                            # 避免同一 Matcher 执行被计数两次
                            return intercept
                    except Exception:
                        logger.exception(
                            f"插件 {plugin.name} before_handler 异常: "
                            f"{getattr(before_fn, '__name__', repr(before_fn))}"
                        )

            # 执行 handler（支持参数级依赖注入：按签名解析 MatcherContext、Bot、DI 服务等）
            from ..core.di import resolve_handler_args

            args, kwargs = await resolve_handler_args(
                matcher.handler, context=mctx, bot=bot, container=bot.di
            )
            result = matcher.handler(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result

            # 插件级 after_handler 中间件
            if plugin is not None:
                for after_fn in plugin._after_handlers:
                    try:
                        modified = after_fn(matcher, mctx, result)
                        if hasattr(modified, "__await__"):
                            modified = await modified
                        if modified is not None:
                            result = modified
                    except Exception:
                        logger.exception(
                            f"插件 {plugin.name} after_handler 异常: "
                            f"{getattr(after_fn, '__name__', repr(after_fn))}"
                        )

            return result

        except Exception:
            is_error = True
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            bot.plugin_manager.record_metric(matcher, elapsed, is_error=is_error)

    def _parse_message(self, event: dict) -> MessageContext:
        """解析 OneBot 11 消息事件（v11 兼容路径，保留供测试与防御性兜底）

        消息段统一归一化为 OneBot 12 段存储（Message.from_raw 自动识别
        v11 段：at -> mention、record -> voice 等）；
        at_list 保持 v11 语义（"0" 表示 @全体），is_at_bot 用 self_id 匹配。
        """
        ctx = MessageContext(raw_event=event)

        ctx.post_type = event.get("post_type", "")
        ctx.message_type = event.get("message_type", "")
        ctx.sub_type = event.get("sub_type", "")
        # 消息缺失 message_id 时生成占位 id，避免空串在 messages 表的
        # unique 约束下被静默丢弃（第二条起全部冲突）；
        # DB 迁移为组合索引（M2-4）后可移除该占位
        raw_mid = event.get("message_id")
        if raw_mid not in (None, "", 0):
            ctx.message_id = str(raw_mid)
        else:
            ctx.message_id = f"gen-{event.get('user_id', 0)}-{int(time.time() * 1000)}"
        ctx.self_id = self._safe_int(event.get("self_id"))
        ctx.user_id = self._safe_int(event.get("user_id"))
        ctx.group_id = self._safe_int(event.get("group_id"))
        ctx.sender = event.get("sender", {}) or {}
        ctx.raw_message = event.get("raw_message", "")

        # 段归一化：字符串 / v11 段数组统一转为 v12 段数组
        msg = Message.from_raw(event.get("message"))
        ctx.segments = msg.as_dicts()
        ctx.plain_text = msg.extract_plain_text()

        # mention 统计（v11 at 段已由 Message 归一化为 mention/mention_all）
        self_id_str = str(ctx.self_id or "")
        for seg in ctx.segments:
            seg_type = seg.get("type", "")
            data = seg.get("data", {})
            if not isinstance(data, dict):
                data = {}
            if seg_type == "mention":
                uid = str(data.get("user_id", "") or "")
                if uid:
                    ctx.at_list.append(uid)
                    if self_id_str and uid == self_id_str:
                        ctx.is_at_bot = True
            elif seg_type == "mention_all":
                # "0" 表示全体成员（v11 语义，与 v12 路径字符串列表一致）
                ctx.at_list.append("0")
            elif seg_type == "image":
                ctx.images.append(str(data.get("file_id") or data.get("url") or ""))

        return ctx

    @staticmethod
    def build_cq_at(qq: int) -> str:
        """构建 CQ @ 码"""
        return f"[CQ:at,qq={qq}]"

    @staticmethod
    def build_cq_image(file: str) -> str:
        """构建 CQ 图片码"""
        return f"[CQ:image,file={file}]"

    @staticmethod
    def build_cq_reply(message_id: str) -> str:
        """构建 CQ 回复码"""
        return f"[CQ:reply,id={message_id}]"
