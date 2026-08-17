"""Telegram 平台适配器 — Bot API 长轮询

将 Telegram Update 归一化为 OneBot-11 兼容事件 dict（含 platform=telegram），
使 Qingci-Bot 的插件/内置功能对 Telegram 零改动可用：

- 长轮询 getUpdates（httpx 异步，poll_interval 间隔，offset 游标续传）
- 消息事件：message → post_type=message；私聊/群聊 → message_type；
  user_id（from.id）、group_id（chat.id，仅群聊）、message_id、文本段
  （CQ 纯文本段）、raw_message、sender（first_name/username）
- @提及：解析 entities（mention / text_mention）识别群聊中的 @Bot，
  命中时写入 at 段（qq=self_id）供 at 触发使用；私聊天然放行
- 图片：photo / 图片 document → image 段 + images（file_id）
- 发送：send_msg 走 sendMessage（Telegram 统一 chat_id）并识别
  [CQ:image] 转发 sendPhoto（file_id / http(s) URL / base64 / 本地路径）；
  group/private 均按 chat_id 发送；其余 CQ 段降级为纯文本
- 能力：call_api 映射 send_private_msg/send_group_msg → sendMessage/sendPhoto，
  其余 action 透传为 Telegram 方法（小写方法名）
- 状态：轮询运行即视为已连接；last_heartbeat 随每次成功轮询更新

当前一期仅处理 message 类事件；notice/request 类型（如成员变动）
预留接口，后续按需补充。
"""

import asyncio
import base64
import logging
import mimetypes
import re
import time
from pathlib import Path

import httpx

from .base import PlatformAdapter, cancel_and_await

logger = logging.getLogger("qingci-bot.platforms.telegram")

# Telegram Bot API 基础地址
API_BASE = "https://api.telegram.org/bot{token}/{method}"

# 长轮询超时（秒）：超过则 Telegram 侧保持连接等待新消息
POLL_TIMEOUT = 30
# 单次轮询最大更新数
POLL_LIMIT = 100

# CQ 图片段：capture file 引用
_CQ_IMAGE_RE = re.compile(r"\[CQ:image,file=([^,\]$]+)\]")
# 任意 CQ 段（用于把不可渲染的段从发送文本中剔除）
_ANY_CQ_RE = re.compile(r"\[CQ:[^\[\]]*\]")


class TelegramAdapter(PlatformAdapter):
    """Telegram Bot API 长轮询适配器"""

    name = "telegram"
    display_name = "Telegram"

    def __init__(self, token: str = "", *, poll_interval: float = 1.0):
        super().__init__()
        self.token = str(token or "").strip()
        self.poll_interval = max(0.5, float(poll_interval or 1.0))
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._offset = 0
        self._last_heartbeat = 0.0
        self.self_id = 0
        self.username = ""  # Bot @用户名（无 @ 前缀），用于 at 提及识别
        self._http: httpx.AsyncClient | None = None

    # ============ HTTP ============

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(POLL_TIMEOUT + 10))
        return self._http

    async def _api(self, method: str, *, files: dict | None = None, **params) -> dict:
        """调用 Telegram Bot API，返回 result 字段

        files 非空时走 multipart（data=文本字段 + files=上传文件），
        否则走 JSON。
        """
        if not self.token:
            raise RuntimeError("Telegram bot token 未配置（platforms.telegram.token）")
        url = API_BASE.format(token=self.token, method=method)
        if files:
            data = {k: str(v) for k, v in params.items() if v is not None}
            resp = await self._client().post(url, data=data or None, files=files)
        else:
            resp = await self._client().post(url, json=params or None)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API 错误 ({method}): {data.get('description', '')}")
        return data.get("result", {}) or {}

    # ============ 生命周期 ============

    async def start(self) -> None:
        self._running = True
        # 验证 token 并获取 Bot 自身信息（self_id）
        try:
            me = await self._api("getMe")
            self.self_id = self._safe_int(me.get("id"))
            self.username = str(me.get("username", "") or "").strip()
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
        """长轮询循环：getUpdates → 归一化 → emit_event"""
        consecutive_errors = 0
        while self._running:
            try:
                updates = await self._api(
                    "getUpdates",
                    offset=self._offset,
                    timeout=POLL_TIMEOUT,
                    limit=POLL_LIMIT,
                )
                self._last_heartbeat = time.time()
                consecutive_errors = 0
                if isinstance(updates, list):
                    for upd in updates:
                        if not isinstance(upd, dict):
                            continue
                        update_id = self._safe_int(upd.get("update_id"))
                        if update_id >= self._offset:
                            self._offset = update_id + 1
                        await self._handle_update(upd)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"Telegram 轮询异常（第 {consecutive_errors} 次连续）: {e}")
                if consecutive_errors >= 5:
                    # 连续失败说明网络/token 异常，广播断连状态
                    logger.error("Telegram 轮询连续失败，标记平台离线")
                    await self.notify_disconnected()
                await asyncio.sleep(min(30, self.poll_interval * 3))
                continue
            await asyncio.sleep(self.poll_interval)

    async def _handle_update(self, update: dict) -> None:
        """处理单条 Update"""
        message = update.get("message")
        if isinstance(message, dict):
            event = self._normalize_message(message)
            if event is not None:
                await self.emit_event(event)

    # ============ 归一化 ============

    @staticmethod
    def _extract_text(message: dict) -> str:
        """提取消息文本（text / caption / 拼接实体前后缀）"""
        text = message.get("text")
        if text is None:
            text = message.get("caption", "")
        return str(text or "")

    def _normalize_message(self, message: dict) -> dict | None:
        """将 Telegram message 归一化为 OneBot-11 兼容消息事件 dict"""
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_type = str(chat.get("type", ""))
        if chat_type not in ("private", "group", "supergroup"):
            return None
        message_type = "private" if chat_type == "private" else "group"

        # 文本（纯文本 CQ 段）
        text = self._extract_text(message)
        raw_message = text
        segments: list[dict] = []

        # @提及：解析 entities（mention / text_mention）识别 @Bot。
        # 群聊命中时写入 at(self) 段，dispatcher 据此推导 ctx.is_at_bot；
        # 私聊无需 at 段（SDK 触发规则已放行 message_type == "private"）。
        at_segments, at_list = self._collect_at(message, text)
        is_at_bot = bool(at_segments) or message_type == "private"
        segments.extend(at_segments)

        if text:
            segments.append({"type": "text", "data": {"text": text}})

        # 图片：photo（末项最大）或 image/* document
        images: list[str] = []
        image_file = self._extract_image_file(message)
        if image_file:
            images.append(image_file)
            segments.append({"type": "image", "data": {"file": image_file, "url": image_file}})

        event = {
            "post_type": "message",
            "message_type": message_type,
            "sub_type": "normal",
            "message_id": str(message.get("message_id", "")),
            "user_id": self._safe_int(from_user.get("id")),
            "group_id": self._safe_int(chat.get("id")) if message_type == "group" else 0,
            "self_id": getattr(self, "self_id", 0),
            "raw_message": raw_message,
            "message": segments,
            "plain_text": text,
            "at_list": at_list,
            "is_at_bot": is_at_bot,
            "images": images,
            "sender": {
                "user_id": self._safe_int(from_user.get("id")),
                "nickname": str(from_user.get("first_name", "") or ""),
                "card": str(from_user.get("first_name", "") or ""),
                "username": str(from_user.get("username", "") or ""),
                "platform": self.name,
            },
            "platform": self.name,
            # Telegram 原始 chat 信息（供适配器内部/高级插件使用）
            "_telegram_chat": chat,
            "_telegram_message": message,
        }
        return event

    def _collect_at(self, message: dict, text: str) -> tuple[list[dict], list[dict]]:
        """解析 entities，识别 @Bot 与提及的其他用户。

        返回 (at_segments, at_list)：
        - at_segments：可写入 message[] 的 at 段（仅命中 Bot 自身时）；
        - at_list：事件级 at 提及清单（含被 @ 的其他用户，用户名用 qq 占位）。
        """
        at_segments: list[dict] = []
        at_list: list[dict] = []
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
                    at_segments.append({"type": "at", "data": {"qq": self_uid}})
                    at_list.append({"type": "at", "data": {"qq": self_uid}})
                else:
                    # 提及其他用户：仅记录到 at_list，不写入 message 段
                    at_list.append({"type": "at", "data": {"qq": token}})
            elif etype == "text_mention":
                user = ent.get("user") or {}
                uid = self._safe_int(user.get("id"))
                if not uid:
                    continue
                if uid == self_uid:
                    at_segments.append({"type": "at", "data": {"qq": self_uid}})
                    at_list.append({"type": "at", "data": {"qq": self_uid}})
                else:
                    at_list.append({"type": "at", "data": {"qq": uid}})
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

    # ============ 发送 ============

    async def send_msg(self, message_type: str, target_id: int, message: str) -> dict:
        """发送消息：识别 [CQ:image] → sendPhoto，否则 sendMessage"""
        chat_id = self._safe_int(target_id)
        if chat_id <= 0:
            raise ValueError(f"Telegram 发送失败：无效的 chat_id={target_id}")
        return await self._route_send(chat_id, str(message or ""))

    @staticmethod
    def _strip_cq_images(message: str) -> tuple[list[str], str]:
        """从发送内容中提取 [CQ:image,file=..] 的 file 列表与残留纯文本

        残留文本会剔除其余 CQ 段（Telegram 无法渲染的面向其他平台的段），
        并合并连续空白。
        """
        images = _CQ_IMAGE_RE.findall(message or "")
        text = _ANY_CQ_RE.sub("", message or "")
        text = re.sub(r"[ \t\n\r]+", " ", text).strip()
        return images, text

    async def _route_send(self, chat_id: int, message: str) -> dict:
        """按内容路由：含图片 → _send_photo，否则 sendMessage 纯文本"""
        images, text = self._strip_cq_images(message)
        if images:
            return await self._send_photo(chat_id, images, text)
        return await self._api(
            "sendMessage",
            chat_id=chat_id,
            text=text or "",
            disable_web_page_preview=True,
        )

    async def _send_photo(self, chat_id: int, images: list[str], caption: str = "") -> dict:
        """发送图片：file_id/http(s) URL 走 photo 参数，本地/base64 走 multipart 上传"""
        result: dict = {}
        for idx, raw in enumerate(images):
            title = caption if idx == 0 else ""
            kind, photo_val, media = self._resolve_image(raw)
            if kind == "upload":
                assert media is not None
                result = await self._api(
                    "sendPhoto",
                    files={"photo": media},
                    chat_id=chat_id,
                    caption=title or None,
                )
            else:
                result = await self._api(
                    "sendPhoto", photo=photo_val, chat_id=chat_id, caption=title or None
                )
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
        if action == "send_private_msg":
            chat_id = self._safe_int(params.get("user_id"))
            return await self._route_send(chat_id, str(params.get("message", "")))
        elif action == "send_group_msg":
            chat_id = self._safe_int(params.get("group_id"))
            return await self._route_send(chat_id, str(params.get("message", "")))
        elif action == "get_group_info":
            # Telegram 无群资料 API，返回最小兼容结构
            return {"group_id": self._safe_int(params.get("group_id")), "group_name": "Telegram 群"}
        else:
            method, mapped = action, params
            return await self._api(method, **mapped)
