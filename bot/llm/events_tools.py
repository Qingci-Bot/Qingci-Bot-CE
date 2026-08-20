"""类型化事件的 LLM 工具化 — 事件缓冲 + Function Calling 只读工具

notice/request 事件（已类型化，见 qingci_plugin_sdk.events）经 Dispatcher
分发时自动写入 EventBuffer（内存环形缓冲，容量上限防膨胀）。注册到
ToolRegistry 的只读工具让 LLM 在对话中可查询群/成员最近动态：

    get_group_events(group_id, hours, limit)     # 查询某群最近事件
    get_member_events(user_id, group_id, hours, limit)  # 按成员查询

仅记录、仅查询，无任何写操作，与内置只读工具（get_current_time 等）
同一安全级别。缓冲按实例内存持有，Bot 重启即清空（事件为瞬态信息，
不落库、不涉及隐私持久化）。
"""

import logging
import time
from collections import deque

logger = logging.getLogger("qingci-bot.llm.events_tools")


def _safe_int(value) -> int:
    """安全转 int：非数值（字符串/None/空串/浮点串等）回退 0，避免事件整条丢失"""
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# 缓冲容量上限：超出后丢弃最旧事件
DEFAULT_BUFFER_CAPACITY = 200
# 事件类型中文映射（工具返回文本可读性）
_NOTICE_LABELS = {
    "group_increase": "成员入群",
    "group_decrease": "成员退群",
    "group_ban": "禁言",
    "group_admin": "管理员变动",
    "group_recall": "群消息撤回",
    "friend_recall": "好友消息撤回",
    "friend_add": "好友添加",
    "group_upload": "群文件上传",
    "poke": "戳一戳",
}
_REQUEST_LABELS = {
    "friend": "好友请求",
    "group": "加群请求",
}


class EventBuffer:
    """notice/request 事件内存环形缓冲

    线程安全由 asyncio 单线程事件循环保证（Bot 事件处理均在同一循环）。
    """

    def __init__(self, capacity: int = DEFAULT_BUFFER_CAPACITY):
        self.capacity = max(1, int(capacity))
        self._entries: deque[dict] = deque(maxlen=self.capacity)

    def record(self, event) -> None:
        """记录一个类型化事件（NoticeEvent/RequestEvent 或 dict 兜底）"""
        if event is None:
            return
        ts = time.time()
        if isinstance(event, dict):
            raw = dict(event)
            post_type = str(raw.get("post_type", ""))
            entry = {
                "time": ts,
                "post_type": post_type,
                "sub_type": str(raw.get("sub_type", "")),
                "user_id": _safe_int(raw.get("user_id")),
                "group_id": _safe_int(raw.get("group_id")),
                "label": self._label(
                    post_type, str(raw.get("notice_type", raw.get("request_type", "")))
                ),
                "detail": self._summarize_dict(raw),
            }
        else:
            post_type = getattr(event, "post_type", "") or ""
            kind = getattr(event, "notice_type", None) or getattr(event, "request_type", "") or ""
            entry = {
                "time": ts,
                "post_type": post_type,
                "sub_type": getattr(event, "sub_type", "") or "",
                "user_id": _safe_int(getattr(event, "user_id", 0)),
                "group_id": _safe_int(getattr(event, "group_id", 0)),
                "label": self._label(post_type, str(kind)),
                "detail": self._summarize_typed(event),
            }
        self._entries.append(entry)

    # ---- 查询 ----

    def query(
        self,
        *,
        group_id: int | None = None,
        user_id: int | None = None,
        hours: int | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """按维度查询最近事件（新→旧）

        Args:
            group_id: 按群过滤（None 不过滤）
            user_id: 按用户过滤（None 不过滤）
            hours: 只看最近 N 小时内（None 不限）
            limit: 返回条数上限
        """
        cutoff = time.time() - float(hours or 0) * 3600 if hours else 0.0
        matched = [
            e
            for e in self._entries
            if (group_id is None or e["group_id"] == group_id)
            and (user_id is None or e["user_id"] == user_id)
            and (hours is None or e["time"] >= cutoff)
        ]
        matched.reverse()  # 新→旧
        return matched[: max(1, int(limit))]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    # ---- 格式化 ----

    @staticmethod
    def _label(post_type: str, event_kind: str) -> str:
        if post_type == "request":
            return _REQUEST_LABELS.get(event_kind, event_kind or "请求事件")
        return _NOTICE_LABELS.get(event_kind, event_kind or "通知事件")

    def _summarize_typed(self, event) -> str:
        """从类型化事件对象提取可读摘要（按子类字段）"""
        parts: list[str] = []
        group_id = getattr(event, "group_id", 0) or 0
        user_id = getattr(event, "user_id", 0) or 0
        if group_id:
            parts.append(f"群{group_id}")
        if user_id:
            parts.append(f"用户{user_id}")
        sub = getattr(event, "sub_type", "") or ""
        if sub:
            parts.append(f"类型[{sub}]")
        for attr in ("operator_id", "target_id", "message_id", "duration"):
            val = getattr(event, attr, None)
            if val not in (None, 0, ""):
                parts.append(f"{attr}={val}")
        comment = getattr(event, "comment", "") or ""
        if comment:
            parts.append(f"留言[{comment}]")
        if not parts:
            return str(event)
        return "，".join(parts)

    def _summarize_dict(self, raw: dict) -> str:
        """从原始 dict 提取可读摘要（兜底，兼容非 SDK 事件）"""
        parts: list[str] = []
        if raw.get("group_id"):
            parts.append(f"群{raw.get('group_id')}")
        if raw.get("user_id"):
            parts.append(f"用户{raw.get('user_id')}")
        for attr in ("operator_id", "target_id", "message_id", "duration"):
            if raw.get(attr):
                parts.append(f"{attr}={raw.get(attr)}")
        if raw.get("comment"):
            parts.append(f"留言[{raw.get('comment')}]")
        return "，".join(parts) if parts else str(raw)

    def format_entries(self, entries: list[dict]) -> str:
        """将事件条目格式化为 LLM 可读文本"""
        if not entries:
            return "（无相关事件记录）"
        lines = []
        for e in entries:
            stamp = time.strftime("%m-%d %H:%M", time.localtime(e["time"]))
            lines.append(f"- [{stamp}] {e['label']}：{e['detail']}")
        return "\n".join(lines)


def _parse_int(value, default: int = 0) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def register_event_tools(
    registry,
    buffer: EventBuffer,
    *,
    capacity: int = DEFAULT_BUFFER_CAPACITY,
) -> int:
    """向注册表注册事件查询工具（幂等：已存在则跳过）

    Args:
        registry: ToolRegistry 实例
        buffer: EventBuffer 实例（由 Bot 常驻持有）
        capacity: 缓冲容量（仅用于工具描述展示）

    Returns:
        注册的工具数量
    """
    count = 0

    def get_group_events(group_id: int, hours: int = 24, limit: int = 10) -> str:
        """查询指定群最近的通知/请求事件（成员入群/退群/禁言/撤回/文件上传等）"""
        gid = _parse_int(group_id)
        if gid <= 0:
            return "参数错误：group_id 必须是正整数（群号）。"
        h = _parse_int(hours, 24)
        n = min(max(_parse_int(limit, 10), 1), 50)
        entries = buffer.query(group_id=gid, hours=h if h > 0 else None, limit=n)
        return buffer.format_entries(entries)

    def get_member_events(user_id: int, group_id: int = 0, hours: int = 24, limit: int = 10) -> str:
        """查询指定成员最近的通知/请求事件（入群/退群/禁言/戳一戳等）"""
        uid = _parse_int(user_id)
        if uid <= 0:
            return "参数错误：user_id 必须是正整数（QQ 号）。"
        gid = _parse_int(group_id)
        h = _parse_int(hours, 24)
        n = min(max(_parse_int(limit, 10), 1), 50)
        entries = buffer.query(
            user_id=uid,
            group_id=gid if gid > 0 else None,
            hours=h if h > 0 else None,
            limit=n,
        )
        return buffer.format_entries(entries)

    if not registry.has("get_group_events"):
        registry.register(
            name="get_group_events",
            description=(
                f"查询指定群最近的事件动态（成员入群/退群/禁言/管理员变动/"
                f"消息撤回/文件上传等，仅保留最近 {capacity} 条）。"
                f"当用户询问群内发生了什么、谁入群/退群、谁被禁言时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "group_id": {
                        "type": "integer",
                        "description": "群号（QQ 群号）",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "只看最近 N 小时内的记录，默认 24",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认 10，最大 50",
                    },
                },
                "required": ["group_id"],
            },
            handler=get_group_events,
        )
        count += 1

    if not registry.has("get_member_events"):
        registry.register(
            name="get_member_events",
            description=(
                f"查询指定成员最近的事件动态（入群/退群/禁言/戳一戳等，"
                f"仅保留最近 {capacity} 条）。"
                f"当用户询问某成员最近的动作、是否被禁言时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "成员 QQ 号",
                    },
                    "group_id": {
                        "type": "integer",
                        "description": "限定群号（可选，0 或省略表示不限群）",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "只看最近 N 小时内的记录，默认 24",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认 10，最大 50",
                    },
                },
                "required": ["user_id"],
            },
            handler=get_member_events,
        )
        count += 1

    return count


__all__ = [
    "EventBuffer",
    "DEFAULT_BUFFER_CAPACITY",
    "register_event_tools",
    "_NOTICE_LABELS",
    "_REQUEST_LABELS",
]
