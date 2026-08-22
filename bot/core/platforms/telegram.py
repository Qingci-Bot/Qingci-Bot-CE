"""Telegram 平台适配器 — Bot API 长轮询

将 Telegram Update 归一化为 OneBot-12 兼容事件 dict（含 platform=telegram），
使 Qingci-Bot 的插件/内置功能对 Telegram 零改动可用：

- 长轮询 getUpdates（httpx 异步，poll_interval 间隔，offset 游标续传）
- 消息事件：message → type=message；私聊/群聊 → detail_type；
  user_id（from.id）、group_id（chat.id，仅群聊）、message_id、文本段
  （v12 text 段）、alt_message、sender（first_name/username）
- @提及：解析 entities（mention / text_mention）识别群聊中的 @Bot，
  命中时写入 v12 mention 段（user_id=self_id）供 at 触发使用；私聊天然放行
- 媒体：photo / 图片 document → image 段 + images（file_id）；
  voice → voice 段，video / video_note → video 段
- 发送：send_msg 消费 v12 消息段并识别 image/voice/video → sendPhoto /
  sendVoice / sendVideo（file_id / http(s) URL / base64 / 本地路径），
  group/private 均按 chat_id 发送；reply 段 → 回复指定消息；
  其余不可渲染段降级为纯文本
- 通知：chat_member / my_chat_member 成员变动 → group_member_increase /
  group_member_decrease / group_admin_set / group_admin_unset notice 事件
- 特有事件补全（归一化为 OneBot 12 扩展 notice，插件用 on_notice 消费）：
  edited_message → message_edited（携带新文本与段数组，不触发消息回复）；
  callback_query → callback_query（携带 data / callback_query_id，
  可经 call_api("answer_callback_query") 应答）；message_reaction →
  message_reaction（新/旧表情列表，sub_type 区分 add/remove/change）
- 能力：call_api 映射 send_private_msg/send_group_msg → sendMessage/sendPhoto/sendVoice/sendVideo，
  其余 action 透传为 Telegram 方法（小写方法名）
- 状态：轮询运行即视为已连接；last_heartbeat 随每次成功轮询更新

说明：Telegram 的 edited_message / callback_query / message_reaction 承载
其特有语义，在 OneBot 事件模型中无等价事件，以扩展 notice detail_type
（message_edited / callback_query / message_reaction）承载，插件可用
on_notice() 消费。offset 游标采用整批确认：先以 max(update_id)+1 推进
再消费本批，处理失败也已确认，避免无限重放。
"""

import asyncio
import base64
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx
from qingci_plugin_sdk.segments import Message

from .base import PlatformAdapter, cancel_and_await

logger = logging.getLogger("qingci-bot.platforms.telegram")

# Telegram Bot API 基础地址
API_BASE = "https://api.telegram.org/bot{token}/{method}"

# 长轮询超时（秒）：超过则 Telegram 侧保持连接等待新消息
POLL_TIMEOUT = 30
# 单次轮询最大更新数
POLL_LIMIT = 100

# 断连指数退避：起始/上限间隔（秒）
_BACKOFF_MIN = 1.5
_BACKOFF_MAX = 60.0
# 连续失败达到该值即判定离线并广播断连
_OFFLINE_AFTER = 5

# OneBot 媒体段 → Telegram 发送方法与字段名（v12 为 voice；兼容 v11 record 兜底）
_MEDIA_API: dict[str, tuple[str, str]] = {
    "image": ("sendPhoto", "photo"),
    "voice": ("sendVoice", "voice"),
    "record": ("sendVoice", "voice"),
    "video": ("sendVideo", "video"),
}

# OneBot 群管/成员动作前缀：Telegram 无对应能力，call_api 应明确报错而非透传
# （透传会变成小写 Telegram 方法名 → 404 晦涩错误）。
# 注意：send_private_msg / send_group_msg / get_group_info 已有专属映射，不受影响。
_UNSUPPORTED_OB_ACTION_PREFIXES = ("set_group_", "get_group_member_", "set_friend_", "get_friend_")


class TelegramAPIError(RuntimeError):
    """Telegram Bot API 调用失败"""

    def __init__(self, message: str, *, error_code: int = 0, description: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.description = description


class TelegramUnauthorizedError(TelegramAPIError):
    """Bot Token 无效/过期（error_code=401）"""


class TelegramForbiddenError(TelegramAPIError):
    """Bot 被禁言 / 无权限 / 目标不可达（error_code=403）"""


class TelegramNotFoundError(TelegramAPIError):
    """聊天/文件不存在或用户不可达（error_code=404）"""


def _coerce_code(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _telegram_error(method: str, data: dict) -> TelegramAPIError:
    """按 Telegram error_code 归类调用失败，便于上层区分处理"""
    code = _coerce_code(data.get("error_code"))
    desc = str(data.get("description") or "") or "No description"
    cls = TelegramAPIError
    if code == 401:
        cls = TelegramUnauthorizedError
    elif code == 403:
        cls = TelegramForbiddenError
    elif code == 404:
        cls = TelegramNotFoundError
    return cls(f"Telegram API 错误 ({method}): {desc}", error_code=code, description=desc)


class TelegramAdapter(PlatformAdapter):
    """Telegram Bot API 长轮询适配器"""

    name = "telegram"
    display_name = "Telegram"

    # 单批内更新的有限并发数：避免单条慢处理阻塞同批其他更新，同时防无限并发
    _MAX_CONCURRENT_UPDATES = 8

    def __init__(
        self,
        token: str = "",
        *,
        poll_interval: float = 1.0,
        request_timeout: float | None = None,
        max_retries: int = 0,
    ):
        super().__init__()
        self.token = str(token or "").strip()
        self.poll_interval = max(0.5, float(poll_interval or 1.0))
        # httpx 客户端超时（秒）；须大于长轮询 timeout，否则会被提前打断
        self.request_timeout = max(
            10.0, float(request_timeout if request_timeout is not None else POLL_TIMEOUT + 10)
        )
        # 网络传输错误（httpx.TransportError）最多重试次数；默认 0 不重试，避免发送类重复
        self.max_retries = max(0, int(max_retries or 0))
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._offset = 0
        self._last_heartbeat = 0.0
        self.self_id = 0
        self.username = ""  # Bot @用户名（无 @ 前缀），用于 at 提及识别
        self._http: httpx.AsyncClient | None = None
        # 有限并发消费信号量：单批更新内并发度上限（见 _MAX_CONCURRENT_UPDATES）
        self._update_sem = asyncio.Semaphore(self._MAX_CONCURRENT_UPDATES)
        # --- 可观测性 / 连接健康 ---
        self.consecutive_errors = 0  # 当前连续失败次数
        self.error_count = 0  # 累计错误次数
        self.last_error_time = 0.0  # 最近一次失败的 epoch 秒
        self.last_disconnect_time = 0.0  # 最近一次判定离线的 epoch 秒
        self._identity_dirty = False  # Token 热更新后待重验身份
        self._disconnected_notified = False  # 是否已广播过断连（避免重复触发）

    # ============ HTTP ============

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout))
        return self._http

    async def _api(self, method: str, *, files: dict | None = None, **params) -> dict:
        """调用 Telegram Bot API，返回 result 字段

        files 非空时走 multipart（data=文本字段 + files=上传文件），否则走 JSON。
        网络传输错误（httpx.TransportError）按 max_retries 有限重试；
        业务错误（非 2xx / ok=false）不重试。
        """
        if not self.token:
            raise RuntimeError("Telegram bot token 未配置（platforms.telegram.token）")
        url = API_BASE.format(token=self.token, method=method)
        if files:
            payload: dict = {
                "data": {k: str(v) for k, v in params.items() if v is not None} or None,
                "files": files,
            }
        else:
            payload = {"json": params or None, "files": None}
        last_exc: httpx.TransportError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._api_once(url, **payload)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                delay = 0.5 * (attempt + 2)
                logger.warning(
                    "Telegram 网络请求失败，将在 %.1fs 后重试 (method=%s, 第 %d 次): %s",
                    delay,
                    method,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(delay)
        raise TelegramAPIError(f"Telegram 请求失败 ({method}): {last_exc}") from None

    async def _api_once(self, url: str, **payload) -> dict:
        """执行单次 Telegram API 请求并校验 ok 字段（网络错误向上传播供重试）"""
        if payload.get("files"):
            resp = await self._client().post(url, data=payload.get("data"), files=payload["files"])
        else:
            resp = await self._client().post(url, json=payload.get("json"))
        resp.raise_for_status()
        data = resp.json()
        if data is None or data.get("ok") is not True:
            method = url.rsplit("/", 1)[-1]
            raise _telegram_error(method, data if isinstance(data, dict) else {})
        return data.get("result", {}) or {}

    def set_token(self, token: str) -> None:
        """热更新 Bot Token（无需重启适配器，下次调用即生效）

        更新后标记身份待重验，轮询下一轮会自动调用 getMe 刷新 self_id/username。
        """
        token = str(token or "").strip()
        if not token:
            raise ValueError("Telegram Bot Token 不能为空")
        if token != self.token:
            self.token = token
            self._identity_dirty = True
            logger.info("Telegram Bot Token 已热更新，等待重验身份（self_id=%s）", self.self_id)

    async def refresh_identity(self) -> None:
        """调用 getMe 校验 Bot Token 并刷新 self_id/username；失败抛异常"""
        me = await self._api("getMe")
        self.self_id = self._safe_int(me.get("id"))
        self.username = str(me.get("username", "") or "").strip()
        self._identity_dirty = False
        logger.info("Telegram 身份已重验: self_id=%s username=%s", self.self_id, self.username)

    def status_info(self) -> dict:
        """扩展平台状态字段（合并进 get_status 的 platforms 项，供可观测性）"""
        state = "stopped"
        if self._running:
            state = "connecting" if self.consecutive_errors > 0 else "connected"
        return {
            "connection_state": state,
            "consecutive_errors": self.consecutive_errors,
            "error_count": self.error_count,
            "last_error_time": self.last_error_time,
            "last_disconnect_time": self.last_disconnect_time,
            "identity_dirty": self._identity_dirty,
            "backoff": round(self._backoff_delay(), 1),
        }

    def _backoff_delay(self) -> float:
        """下一次失败退避间隔（指数退避，封顶 _BACKOFF_MAX）；无失败则用轮询间隔"""
        if self.consecutive_errors <= 0:
            return self.poll_interval
        return min(_BACKOFF_MAX, _BACKOFF_MIN * (2.0 ** (self.consecutive_errors - 1)))

    # ============ 生命周期 ============

    async def start(self) -> None:
        self._running = True
        # 验证 token 并获取 Bot 自身信息（self_id）
        try:
            await self.refresh_identity()
            username = f"@{self.username}" if self.username else ""
            logger.info(
                f"Telegram 适配器已启动: {username} "
                f"(self_id={self.self_id}, poll_interval={self.poll_interval}s)"
            )
        except Exception as e:
            self._running = False
            raise RuntimeError(f"Telegram 适配器启动失败（token 无效或网络不可达）: {e}") from e
        self._poll_task = asyncio.create_task(self._poll_loop())
        await self.notify_connected()

    async def stop(self) -> None:
        self._running = False
        await cancel_and_await(self._poll_task)
        self._poll_task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        logger.info("Telegram 适配器已停止")

    @property
    def is_connected(self) -> bool:
        return self._running

    @property
    def last_heartbeat(self) -> float:
        return self._last_heartbeat

    # ============ 长轮询 ============

    async def _poll_loop(self) -> None:
        """长轮询循环：getUpdates → 归一化 → emit_event

        成功/失败自适应退避：连续失败指数退避（_BACKOFF_MIN.._BACKOFF_MAX），
        恢复后回到轮询间隔；判定离线后再次成功会广播 on_reconnect。
        Token 热更新（set_token）后自动调用 getMe 重验 self_id/username。
        """
        while self._running:
            try:
                updates = await self._api(
                    "getUpdates",
                    offset=self._offset,
                    timeout=POLL_TIMEOUT,
                    limit=POLL_LIMIT,
                )
                self._last_heartbeat = time.time()
                # 从失败中恢复：清零并广播重连（若此前广播过断连）
                if self.consecutive_errors > 0:
                    if self._disconnected_notified:
                        self._disconnected_notified = False
                        logger.info("Telegram 轮询已恢复，广播重连")
                        await self.notify_reconnected()
                    self.consecutive_errors = 0
                # Token 热更新后自动重验身份（失败仅记日志，下轮再试）
                if self._identity_dirty:
                    try:
                        await self.refresh_identity()
                    except Exception as e:
                        logger.warning("Telegram 身份重验失败，将稍后重试: %s", e)
                if isinstance(updates, list):
                    pending = [u for u in updates if isinstance(u, dict)]
                    if pending:
                        # 先确认整批（推进 offset）再消费：即使某条更新处理失败
                        # 也已被确认，避免同一 update_id 无限重放
                        newest = max(
                            (self._safe_int(u.get("update_id")) for u in pending), default=0
                        )
                        if newest:
                            self._offset = newest + 1
                        # 有限并发消费：慢更新不阻塞同批其他更新；单条失败不拖垮整批
                        await asyncio.gather(*(self._consume_update(u) for u in pending))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.consecutive_errors += 1
                self.error_count += 1
                self.last_error_time = time.time()
                logger.warning("Telegram 轮询异常（第 %d 次连续）: %s", self.consecutive_errors, e)
                if self.consecutive_errors >= _OFFLINE_AFTER and not self._disconnected_notified:
                    self._disconnected_notified = True
                    self.last_disconnect_time = self.last_error_time
                    logger.error("Telegram 轮询连续失败，标记平台离线")
                    await self.notify_disconnected()
                await asyncio.sleep(self._backoff_delay())
                continue
            await asyncio.sleep(self.poll_interval)

    async def _consume_update(self, update: dict) -> None:
        """在信号量控制下消费单条更新；单条失败仅记日志，不拖垮同批其他更新"""
        async with self._update_sem:
            try:
                await self._handle_update(update)
            except Exception as e:
                logger.warning(
                    "处理 Telegram 更新失败（update_id=%s，已确认跳过避免重放）: %s",
                    self._safe_int(update.get("update_id")),
                    e,
                )

    async def _handle_update(self, update: dict) -> None:
        """处理单条 Update：消息/编辑/按钮回调/表情回应/成员变动 → 事件"""
        message = update.get("message")
        if isinstance(message, dict):
            event = self._normalize_message(message)
            if event is not None:
                await self.emit_event(event)
            return

        edited = update.get("edited_message")
        if isinstance(edited, dict):
            notice = self._normalize_edited(update, edited)
            if notice is not None:
                await self.emit_event(notice)
            return

        callback = update.get("callback_query")
        if isinstance(callback, dict):
            notice = self._normalize_callback(update, callback)
            if notice is not None:
                await self.emit_event(notice)
            return

        reaction = update.get("message_reaction")
        if isinstance(reaction, dict):
            notice = self._normalize_reaction(update, reaction)
            if notice is not None:
                await self.emit_event(notice)
            return

        member_key = (
            "chat_member" if isinstance(update.get("chat_member"), dict) else "my_chat_member"
        )
        member_item = update.get(member_key)
        if isinstance(member_item, dict):
            notice = self._normalize_member_notice(
                update=update,
                new_member=member_item.get("new_chat_member"),
                old_member=member_item.get("old_chat_member"),
                chat=member_item.get("chat"),
                operator_user=member_item.get("from"),
                bot_self=(member_key == "my_chat_member"),
            )
            if notice is not None:
                await self.emit_event(notice)

    # Telegram 成员状态集合：member/administrator/creator/restricted 视为在群，
    # left/kicked/未知 视为不在群
    _MEMBER = {"member", "administrator", "creator", "restricted"}
    _ABSENT = {"", "left", "kicked"}

    def _normalize_member_notice(
        self,
        update: dict,
        *,
        new_member: dict | None = None,
        old_member: dict | None = None,
        chat: dict | None = None,
        operator_user: dict | None = None,
        bot_self: bool = False,
    ) -> dict | None:
        """将 chat_member / my_chat_member 更新归一化为 OneBot 12 notice 事件

        群成员增加 → group_member_increase（被邀请时 sub_type=invite）；
        成员离开/被踢 → group_member_decrease（被踢 sub_type=kick）；
        管理员权限变更 → group_admin_set / group_admin_unset。
        """
        if not isinstance(chat, dict):
            return None
        chat_type = str(chat.get("type", ""))
        if chat_type not in ("group", "supergroup"):
            return None
        chat_id = self._safe_int(chat.get("id"))
        if not chat_id:
            return None

        new_member = new_member or {}
        old_member = old_member or {}
        new_status = str(new_member.get("status") or "")
        old_status = str(old_member.get("status") or "")
        if new_status not in self._MEMBER and new_status not in self._ABSENT:
            return None
        if old_status not in self._MEMBER and old_status not in self._ABSENT:
            return None

        new_user = new_member.get("user") or {}
        old_user = old_member.get("user") or {}
        user_id = self._safe_int(new_user.get("id")) or self._safe_int(old_user.get("id"))
        if bot_self:
            user_id = self.self_id
        operator_id = (
            self._safe_int(operator_user.get("id")) if isinstance(operator_user, dict) else 0
        )

        notice = {
            "type": "notice",
            "detail_type": "",
            "sub_type": "normal",
            "id": str(update.get("update_id", "")),
            "impl": self.name,
            "platform": self.name,
            "self_id": str(self.self_id),
            "time": 0,
            "message_id": str(update.get("update_id", "")),
            "user_id": str(user_id),
            "group_id": str(chat_id),
            "operator_id": str(operator_id),
            "_telegram_chat": chat,
            "_telegram_member": new_member or old_member,
        }

        if old_status in self._ABSENT and new_status in self._MEMBER:
            notice["detail_type"] = "group_member_increase"
            # operator 与本人不同视为被邀请，否则为主动加入
            notice["sub_type"] = (
                "invite" if (operator_id and user_id and operator_id != user_id) else "join"
            )
            return notice
        if old_status in self._MEMBER and new_status in self._ABSENT:
            notice["detail_type"] = "group_member_decrease"
            notice["sub_type"] = (
                "kick_me" if bot_self else ("kick" if new_status == "kicked" else "leave")
            )
            return notice

        was_admin = old_status in ("administrator", "creator")
        is_admin = new_status in ("administrator", "creator")
        if not was_admin and is_admin:
            notice["detail_type"] = "group_admin_set"
            notice["sub_type"] = "set"
            return notice
        if was_admin and not is_admin:
            notice["detail_type"] = "group_admin_unset"
            notice["sub_type"] = "unset"
            return notice
        return None

    # ============ 归一化 ============

    @staticmethod
    def _extract_text(message: dict) -> str:
        """提取消息文本（text / caption / 拼接实体前后缀）"""
        text = message.get("text")
        if text is None:
            text = message.get("caption", "")
        return str(text or "")

    def _normalize_message(self, message: dict) -> dict | None:
        """将 Telegram message 归一化为 OneBot 12 消息事件 dict

        产出 v12 事件（type/detail_type，ID 字符串化，段为 v12 标准段）：
        type=message，detail_type=private/group；另携带 alt_message、
        at_list / is_at_bot / images 等便捷字段供上层直接读取。
        """
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_type = str(chat.get("type", ""))
        if chat_type not in ("private", "group", "supergroup"):
            return None
        detail_type = "private" if chat_type == "private" else "group"
        # sub_type 私聊语义三端不同（OB11 真实 friend/group/temp/other、
        # OB12 原生无 sub_type、Telegram 固定 friend）。约定：插件不应依赖
        # 私聊 sub_type 做路由，统一用 message_type == "private"。
        sub_type = "friend" if detail_type == "private" else "normal"

        # 文本（v12 text 段）
        text = self._extract_text(message)
        raw_message = text
        segments: list[dict] = []

        # @提及：解析 entities（mention / text_mention）识别 @Bot。
        # 群聊命中时写入 mention 段，dispatcher 据此推导 ctx.is_at_bot；
        # 私聊无需 mention 段（SDK 触发规则已放行 message_type == "private"）。
        at_segments, at_list = self._collect_at(message, text)
        # is_at_bot 为归一化阶段提示字段：SDK from_v12_event 会按
        # self_id in at_list 重算并覆盖（context.py），私聊最终恒为 False
        # （由 to_me 规则的 message_type == "private" 兜底）。此值仅对直接
        # 读取 raw_event 的调用方可见，勿据此做路由判断（跨端对齐见
        # 跨协议一致性审查报告 P3）。
        is_at_bot = bool(at_segments) or detail_type == "private"
        segments.extend(at_segments)

        if text:
            segments.append({"type": "text", "data": {"text": text}})

        # 图片：photo（末项最大）或 image/* document
        images: list[str] = []
        image_file = self._extract_image_file(message)
        if image_file:
            images.append(image_file)
            segments.append({"type": "image", "data": {"file_id": image_file}})

        # 语音 / 视频
        record_file, video_file = self._extract_media_files(message)
        if record_file:
            segments.append({"type": "voice", "data": {"file_id": record_file}})
        if video_file:
            segments.append({"type": "video", "data": {"file_id": video_file}})

        user_id = self._safe_int(from_user.get("id"))
        chip_id = self._safe_int(chat.get("id"))

        event = {
            "type": "message",
            "detail_type": detail_type,
            "sub_type": sub_type,
            "id": str(message.get("message_id", "")),
            "impl": self.name,
            "platform": self.name,
            "self_id": str(getattr(self, "self_id", 0) or ""),
            "time": message.get("date", 0),
            "message_id": str(message.get("message_id", "")),
            "message": segments,
            "alt_message": raw_message,
            "user_id": str(user_id),
            "group_id": str(chip_id) if detail_type == "group" else "",
            "sender": {
                "user_id": str(user_id),
                "nickname": str(from_user.get("first_name", "") or ""),
                "card": str(from_user.get("first_name", "") or ""),
                "username": str(from_user.get("username", "") or ""),
                "platform": self.name,
            },
            # 便捷字段（与 v11 输出保持同一语义，供上层/插件直接读取）
            "raw_message": raw_message,
            "plain_text": text,
            "at_list": at_list,
            "is_at_bot": is_at_bot,
            "images": images,
            # Telegram 原始 chat 信息（供适配器内部/高级插件使用）
            "_telegram_chat": chat,
            "_telegram_message": message,
        }
        return event

    def _normalize_edited(self, update: dict, message: dict) -> dict | None:
        """将 edited_message 归一化为 notice（detail_type=message_edited）

        消息编辑是 Telegram 特有语义，OneBot 事件模型无等价事件，故以
        扩展 notice 承载。编辑不触发消息回复（避免 bot 对旧消息重复响应），
        插件可用 on_notice() 消费并读取新文本（alt_message）与段数组。
        """
        event = self._normalize_message(message)
        if event is None:
            return None
        return {
            "type": "notice",
            "detail_type": "message_edited",
            "sub_type": event.get("sub_type", ""),
            "id": str(update.get("update_id", "")),
            "impl": self.name,
            "platform": self.name,
            "self_id": str(self.self_id),
            "time": 0,
            "message_id": event.get("message_id", ""),
            "user_id": event.get("user_id", ""),
            "group_id": event.get("group_id", ""),
            "alt_message": event.get("alt_message", ""),
            "message": event.get("message", []),
            "is_at_bot": event.get("is_at_bot", False),
            "_telegram_message": message,
        }

    def _normalize_callback(self, update: dict, callback: dict) -> dict | None:
        """将 callback_query 归一化为 notice（detail_type=callback_query）

        按钮回调为 Telegram 特有交互语义：携带 data（按钮数据）与
        callback_query_id（可经 call_api("answer_callback_query") 应答）。
        消息可能为空（纯 inline 按钮），此时无 message_id/group_id。
        """
        user = callback.get("from") or {}
        user_id = self._safe_int(user.get("id"))
        if not user_id:
            return None
        msg = callback.get("message")
        chat = (msg or {}).get("chat") or {}
        chat_id = self._safe_int(chat.get("id"))
        chat_type = str(chat.get("type", ""))
        return {
            "type": "notice",
            "detail_type": "callback_query",
            "sub_type": "button",
            "id": str(update.get("update_id", "")),
            "impl": self.name,
            "platform": self.name,
            "self_id": str(self.self_id),
            "time": 0,
            "message_id": str((msg or {}).get("message_id", "")),
            "user_id": str(user_id),
            "group_id": str(chat_id) if chat_type in ("group", "supergroup") else "",
            "data": str(callback.get("data") or ""),
            "callback_query_id": str(callback.get("id") or ""),
            "_telegram_callback_query": callback,
        }

    def _normalize_reaction(self, update: dict, reaction: dict) -> dict | None:
        """将 message_reaction 归一化为 notice（detail_type=message_reaction）

        表情回应为 Telegram 特有语义：携带新/旧表情列表（reaction /
        old_reaction），sub_type 区分 add / remove / change。
        """
        user = reaction.get("user") or {}
        user_id = self._safe_int(user.get("id"))
        chat = reaction.get("chat") or {}
        chat_id = self._safe_int(chat.get("id"))
        if not user_id or not chat_id:
            return None
        chat_type = str(chat.get("type", ""))

        def _emojis(items: Any) -> list[str]:
            if not isinstance(items, list):
                return []
            return [
                str(e.get("emoji", "")) for e in items if isinstance(e, dict) and e.get("emoji")
            ]

        new_emoji = _emojis(reaction.get("new_reaction"))
        old_emoji = _emojis(reaction.get("old_reaction"))
        if new_emoji and not old_emoji:
            sub_type = "add"
        elif old_emoji and not new_emoji:
            sub_type = "remove"
        else:
            sub_type = "change"
        return {
            "type": "notice",
            "detail_type": "message_reaction",
            "sub_type": sub_type,
            "id": str(update.get("update_id", "")),
            "impl": self.name,
            "platform": self.name,
            "self_id": str(self.self_id),
            "time": 0,
            "message_id": str(reaction.get("message_id", "")),
            "user_id": str(user_id),
            "group_id": str(chat_id) if chat_type in ("group", "supergroup") else "",
            "reaction": new_emoji,
            "old_reaction": old_emoji,
            "_telegram_reaction": reaction,
        }

    def _collect_at(self, message: dict, text: str) -> tuple[list[dict], list[str]]:
        """解析 entities，识别 @Bot 与提及的其他用户。

        返回 (at_segments, at_list)：
        - at_segments：可写入 message[] 的 v12 mention 段（仅命中 Bot 自身时）；
        - at_list：事件级 @ 提及清单（含被 @ 的其他用户，user_id 字符串）。
        """
        at_segments: list[dict] = []
        at_list: list[str] = []
        self_uname = (self.username or "").lower()
        self_uid = getattr(self, "self_id", 0)
        entities = message.get("entities") or []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            etype = ent.get("type")
            if etype == "mention":
                offset, length = ent.get("offset", 0), ent.get("length", 0)
                token = str(text or "")[int(offset) : int(offset) + int(length)]
                token = token.lstrip("@").rstrip()
                if not token:
                    continue
                if token.lower() == self_uname:
                    at_segments.append({"type": "mention", "data": {"user_id": str(self_uid)}})
                    at_list.append(str(self_uid))
                else:
                    # 提及其他用户：仅记录到 at_list，不写入 message 段
                    at_list.append(token)
            elif etype == "text_mention":
                user = ent.get("user") or {}
                uid = self._safe_int(user.get("id"))
                if not uid:
                    continue
                if uid == self_uid:
                    at_segments.append({"type": "mention", "data": {"user_id": str(self_uid)}})
                    at_list.append(str(self_uid))
                else:
                    at_list.append(str(uid))
        return at_segments, at_list

    @staticmethod
    def _extract_image_file(message: dict) -> str:
        """从 message 提取图片 file_id（photo 末项最大，或 image/* document）"""
        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            return str(photo[-1].get("file_id") or "")
        doc = message.get("document")
        if isinstance(doc, dict) and str(doc.get("mime_type") or "").startswith("image/"):
            return str(doc.get("file_id") or "")
        return ""

    @staticmethod
    def _extract_media_files(message: dict) -> tuple[str, str]:
        """从 message 提取语音 / 视频 file_id（voice → record，video/video_note → video）"""
        record = ""
        voice = message.get("voice")
        if isinstance(voice, dict):
            record = str(voice.get("file_id") or "")
        video = ""
        note = message.get("video_note")
        if isinstance(note, dict):
            video = str(note.get("file_id") or "")
        vid = message.get("video")
        if isinstance(vid, dict) and not video:
            video = str(vid.get("file_id") or "")
        return record, video

    # ============ 发送 ============

    async def send_msg(self, message_type: str, target_id: int, message: str | list) -> dict:
        """发送消息：识别 image/voice/video 段 → sendPhoto/sendVoice/sendVideo，否则 sendMessage

        OneBot 12 迁移：message 为文本或 v12 消息段数组，直接消费段发送
        （不再经 CQ 字符串编解码）。
        """
        chat_id = self._safe_int(target_id)
        if chat_id <= 0:
            raise ValueError(f"Telegram 发送失败：无效的 chat_id={target_id}")
        return await self._route_send(chat_id, message)

    @staticmethod
    def _parse_v12_segments(message: str | list) -> tuple[list[dict], str, int]:
        """把 v12 消息段数组解析为 (media, text, reply_id)

        - media：list[{type: image/voice/record/video, file: file_id}]；
        - reply_id：reply 段的 message_id，用于回复指定消息；
        - text：拼接 text 段并将 mention/mention_all 渲染为可见文本；
          不可渲染的段（如 face/forward）降级忽略。
        """
        media: list[dict] = []
        reply_id = 0
        text_parts: list[str] = []
        for seg in Message.from_raw(message).as_dicts():
            seg_type = seg.get("type", "")
            data = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}
            if seg_type in _MEDIA_API:
                f = str(data.get("file_id") or data.get("file") or data.get("url") or "")
                if f:
                    media.append({"type": seg_type, "file": f})
            elif seg_type == "reply":
                # 跨协议一致性：非数字 message_id（OB12 字符串 id / gen- 派生 id）
                # 无法作为 Telegram reply_to_message_id，静默丢弃引用，
                # 避免 int() 解析失败或误引用到消息 0。
                raw_reply_id = str(data.get("message_id") or data.get("id") or "").strip()
                if raw_reply_id.isdigit():
                    reply_id = int(raw_reply_id)
            elif seg_type == "text":
                text_parts.append(str(data.get("text", "")))
            elif seg_type == "mention":
                # 跨平台降级约定：Telegram 无稳定的 user_id→username 解析，
                # mention 渲染为可见文本 @<id>（不触发真实 @ 通知）；
                # OB11/12 为真实 @。插件不应依赖 mention 在 Telegram 触发通知。
                text_parts.append(f"@{data.get('user_id', '')}")
            elif seg_type == "mention_all":
                text_parts.append("@所有人")
        text = re.sub(r"[ \t\n\r]+", " ", "".join(text_parts)).strip()
        return media, text, reply_id

    async def _route_send(self, chat_id: int, message: str | list) -> dict:
        """按内容路由：含图片/语音/视频 → _send_media，否则 sendMessage 纯文本"""
        media, text, reply_id = self._parse_v12_segments(message)
        if media:
            return await self._send_media(chat_id, media, text, reply_id)
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text or "",
            "disable_web_page_preview": True,
        }
        if reply_id:
            params["reply_to_message_id"] = reply_id
        return await self._api("sendMessage", **params)

    async def _send_media(
        self,
        chat_id: int,
        media: list[dict],
        caption: str = "",
        reply_id: int = 0,
    ) -> dict:
        """发送媒体：file_id/http(s) URL 走参数，本地/base64 走 multipart 上传

        image → sendPhoto，record → sendVoice，video → sendVideo；
        caption 附着于首条媒体，reply_id 命中时回复指定消息。
        """
        result: dict = {}
        for idx, seg in enumerate(media):
            action, field = _MEDIA_API.get(str(seg.get("type") or ""), _MEDIA_API["image"])
            params: dict[str, Any] = {"chat_id": chat_id}
            if caption and idx == 0:
                params["caption"] = caption
            if reply_id:
                params["reply_to_message_id"] = reply_id
            kind, val, media_file = self._resolve_image(str(seg.get("file") or ""))
            if kind == "upload":
                assert media_file is not None
                result = await self._api(action, files={field: media_file}, **params)
            else:
                params[field] = val
                result = await self._api(action, **params)
        return result or {}

    @staticmethod
    def _resolve_image(file: str) -> tuple[str, str, tuple[str, bytes, str] | None]:
        """把 CQ image 的 file 引用解析为 (kind, param_value, media)

        kind:
        - "param"：直接作为 sendPhoto 的 photo 参数（http(s) URL / Telegram file_id）
        - "upload"：需 multipart 上传（base64 / data URI / 本地路径），media=(name, bytes, mime)
        """
        f = str(file or "").strip()
        if f.startswith(("http://", "https://")):
            return "param", f, None
        if f.startswith("base64://"):
            payload = f[len("base64://") :]
            content = base64.b64decode(payload)
            return "upload", "", ("image.bin", content, "application/octet-stream")
        if f.startswith("data:"):
            header, _, payload = f.partition(",")
            mime = header[len("data:") :].split(";")[0] or "application/octet-stream"
            ext = mime.split("/")[-1] if "/" in mime else "bin"
            return "upload", "", (f"image.{ext}", base64.b64decode(payload), mime)
        p = Path(f)
        if p.exists() and p.is_file():
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return "upload", "", (p.name, p.read_bytes(), mime)
        # 其余视为 Telegram file_id（params 传 file_id）
        return "param", f, None

    # ============ 能力透传 ============

    async def call_api(self, action: str, params: dict | None = None, timeout: float = 30) -> dict:
        """映射 OneBot action → Telegram 方法；其余透传为 Telegram 方法名"""
        params = params or {}
        # 平台接口调用钩子（on_calling_api）
        for hook in list(self._api_call_hooks):
            try:
                modified = hook(action, dict(params))
                if asyncio.iscoroutine(modified) or hasattr(modified, "__await__"):
                    modified = await modified
                if modified is not None:
                    params = modified
            except Exception:
                logger.exception(f"平台接口调用钩子异常: {action}")
                raise
        if action in ("send_private_msg", "send_group_msg"):
            key = "user_id" if action == "send_private_msg" else "group_id"
            chat_id = self._safe_int(params.get(key))
            return await self._route_send(chat_id, params.get("message", ""))
        elif action == "get_group_info":
            # Telegram 无群资料 API，返回最小兼容结构
            return {"group_id": self._safe_int(params.get("group_id")), "group_name": "Telegram 群"}
        elif action.startswith(_UNSUPPORTED_OB_ACTION_PREFIXES):
            # Telegram 无群管/成员能力：明确报错而非透传成小写方法名（会 404）
            raise NotImplementedError(f"Telegram 不支持 OneBot 动作: {action}")
        else:
            method, mapped = action, params
            return await self._api(method, **mapped)
