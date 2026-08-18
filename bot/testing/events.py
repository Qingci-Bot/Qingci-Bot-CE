"""测试事件构造器 — 快速生成 OneBot 事件（v11 / v12 双模）

OneBot 12 迁移（方案 A）：
- make_* 系列构造 v11 事件（存量测试与第三方插件测试使用）
- make_v12_* 系列构造 v12 事件（type / detail_type），供迁移后的
  核心路径测试使用

用法：
    from bot.testing import private_message, make_v12_message_event

    event = private_message("你好", user_id=10001)
    v12 = make_v12_message_event("你好", user_id="10001", detail_type="group")
"""


def _v11_sender(user_id: int, group: bool = False) -> dict:
    sender: dict = {"user_id": user_id, "nickname": f"user-{user_id}"}
    if group:
        sender.update({"card": "", "role": "member"})
    return sender


def make_message_event(
    text: str = "",
    *,
    user_id: int = 10001,
    group_id: int = 0,
    message_type: str = "private",
    self_id: int = 20002,
    at_bot: bool = False,
    images: list[str] | None = None,
    message_id: str | None = None,
    sender: dict | None = None,
) -> dict:
    """构造一条 OneBot v11 消息事件

    Args:
        text: 消息纯文本
        user_id: 发送者 QQ
        group_id: 群号（message_type="group" 时使用）
        message_type: private / group
        self_id: Bot 自己的 QQ 号
        at_bot: 是否 @Bot（构造 at 段，供 to_me 规则命中）
        images: 附加图片 URL 列表（构造 image 段）
        message_id: 消息 ID，缺省自动生成
        sender: 发送者信息，缺省自动构造
    """
    segments: list[dict] = []
    if at_bot:
        segments.append({"type": "at", "data": {"qq": str(self_id)}})
    if text:
        segments.append({"type": "text", "data": {"text": text}})
    for url in images or []:
        segments.append({"type": "image", "data": {"url": url}})

    if message_type == "group":
        base = {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "group_id": group_id,
            "sender": sender
            or {
                "user_id": user_id,
                "nickname": f"user-{user_id}",
                "card": "",
                "role": "member",
            },
        }
    else:
        base = {
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "sender": sender or {"user_id": user_id, "nickname": f"user-{user_id}"},
        }

    base.update(
        {
            "user_id": user_id,
            "self_id": self_id,
            "message_id": message_id or f"test-{user_id}-{text[:8] or 'empty'}",
            "raw_message": text,
            "message": segments,
        }
    )
    return base


def private_message(
    text: str = "",
    *,
    user_id: int = 10001,
    self_id: int = 20002,
    at_bot: bool = False,
    images: list[str] | None = None,
) -> dict:
    """构造私聊消息事件"""
    return make_message_event(
        text,
        user_id=user_id,
        message_type="private",
        self_id=self_id,
        at_bot=at_bot,
        images=images,
    )


def group_message(
    text: str = "",
    *,
    user_id: int = 10001,
    group_id: int = 20001,
    self_id: int = 20002,
    at_bot: bool = False,
    images: list[str] | None = None,
) -> dict:
    """构造群聊消息事件"""
    return make_message_event(
        text,
        user_id=user_id,
        group_id=group_id,
        message_type="group",
        self_id=self_id,
        at_bot=at_bot,
        images=images,
    )


def make_notice_event(
    notice_type: str,
    *,
    user_id: int = 10001,
    group_id: int = 0,
    self_id: int = 20002,
    **extra: object,
) -> dict:
    """构造通知事件（notice）

    常用 notice_type: group_increase / group_decrease / group_admin /
    group_upload / friend_add / group_recall / friend_recall
    """
    event: dict = {
        "post_type": "notice",
        "notice_type": notice_type,
        "user_id": user_id,
        "self_id": self_id,
    }
    if group_id:
        event["group_id"] = group_id
    event.update(extra)
    return event


def make_request_event(
    request_type: str,
    *,
    user_id: int = 10001,
    group_id: int = 0,
    self_id: int = 20002,
    flag: str = "test-flag",
    sub_type: str = "add",
    **extra: object,
) -> dict:
    """构造请求事件（request）

    常用 request_type: friend / group
    """
    event: dict = {
        "post_type": "request",
        "request_type": request_type,
        "user_id": user_id,
        "self_id": self_id,
        "flag": flag,
        "sub_type": sub_type,
    }
    if group_id:
        event["group_id"] = group_id
    event.update(extra)
    return event


# ============ OneBot 12 事件构造器 ============


def make_v12_message_event(
    text: str = "",
    *,
    user_id: str = "10001",
    group_id: str = "",
    detail_type: str = "private",
    self_id: str = "20002",
    at_bot: bool = False,
    images: list[str] | None = None,
    message_id: str | None = None,
    sender: dict | None = None,
    platform: str = "onebot",
) -> dict:
    """构造一条 OneBot 12 消息事件

    Args:
        text: 消息纯文本
        user_id: 发送者 ID（字符串）
        group_id: 群 ID（detail_type="group" 时使用）
        detail_type: private / group / guild.message
        self_id: 机器人自身 ID（字符串）
        at_bot: 是否 mention Bot（构造 mention 段，供 to_me 规则命中）
        images: 附加图片 file_id 列表（构造 image 段）
        message_id: 消息 ID，缺省自动生成
        platform: 平台名（默认 onebot）
    """
    segments: list[dict] = []
    if at_bot:
        segments.append({"type": "mention", "data": {"user_id": self_id}})
    if text:
        segments.append({"type": "text", "data": {"text": text}})
    for fid in images or []:
        segments.append({"type": "image", "data": {"file_id": fid}})

    mid = message_id or f"test-{user_id}-{text[:8] or 'empty'}"
    event: dict = {
        "type": "message",
        "detail_type": detail_type,
        "sub_type": "",
        "id": f"evt-{mid}",
        "impl": "test",
        "platform": platform,
        "self_id": self_id,
        "user_id": user_id,
        "message_id": mid,
        "message": segments,
        "alt_message": text,
        "sender": sender or {"user_id": user_id, "nickname": f"user-{user_id}"},
    }
    if detail_type == "group":
        event["group_id"] = group_id or "20001"
    elif detail_type.startswith("guild."):
        event["guild_id"] = "g-1"
        event["channel_id"] = "c-1"
    return event


def make_v12_notice_event(
    detail_type: str,
    *,
    user_id: str = "10001",
    group_id: str = "",
    self_id: str = "20002",
    **extra: object,
) -> dict:
    """构造 OneBot 12 通知事件（type=notice）

    常用 detail_type: group_member_increase / group_member_decrease /
    group_message_delete / group_admin_set / friend_increase ...
    """
    event: dict = {
        "type": "notice",
        "detail_type": detail_type,
        "sub_type": "",
        "id": f"evt-notice-{detail_type}",
        "impl": "test",
        "platform": "onebot",
        "self_id": self_id,
        "user_id": user_id,
    }
    if group_id:
        event["group_id"] = group_id
    event.update(extra)
    return event


def make_v12_request_event(
    detail_type: str,
    *,
    user_id: str = "10001",
    group_id: str = "",
    self_id: str = "20002",
    flag: str = "test-flag",
    **extra: object,
) -> dict:
    """构造 OneBot 12 请求事件（type=request）

    常用 detail_type: friend / group
    """
    event: dict = {
        "type": "request",
        "detail_type": detail_type,
        "sub_type": "",
        "id": f"evt-request-{detail_type}",
        "impl": "test",
        "platform": "onebot",
        "self_id": self_id,
        "user_id": user_id,
        "flag": flag,
    }
    if group_id:
        event["group_id"] = group_id
    event.update(extra)
    return event
