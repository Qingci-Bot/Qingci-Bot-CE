"""测试事件构造器 — 快速生成 OneBot v11 事件

供插件测试工具与第三方插件测试使用：
    from bot.testing import make_message_event, private_message, group_message

    event = private_message("你好", user_id=10001)
    event = group_message("你好", user_id=10001, group_id=20001, at_bot=True)
"""

from typing import Optional


def make_message_event(
    text: str = "",
    *,
    user_id: int = 10001,
    group_id: int = 0,
    message_type: str = "private",
    self_id: int = 20002,
    at_bot: bool = False,
    images: Optional[list[str]] = None,
    message_id: Optional[str] = None,
    sender: Optional[dict] = None,
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
            "sender": sender or {
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
    images: Optional[list[str]] = None,
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
    images: Optional[list[str]] = None,
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