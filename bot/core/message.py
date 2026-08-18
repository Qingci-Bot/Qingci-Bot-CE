"""类型化消息构造器 - 借鉴 NoneBot2 的 Message / MessageSegment 设计

提供类型化的消息段构造（text / at / image / face / voice / video /
reply / forward），统一 CQ 码转义，替代手写 CQ 码字符串拼接。

OneBot 12 迁移（方案 A）：本模块的 Message/MessageSegment 与 CQ 序列化
仅保留给 OneBot-11 平台（aiocqhttp / telegram 旧路径）使用；
框架核心的消息段统一由 SDK 的 qingci_plugin_sdk.segments 承担（v12 段）。

用法:
    from bot.core.message import Message, MessageSegment

    # 文本 + 图片 + 回复组合
    msg = Message(
        MessageSegment.reply("12345"),
        MessageSegment.at(10001),
        MessageSegment.text("看图"),
        MessageSegment.image("https://example.com/a.jpg"),
    )
    await bot.connection.send_msg("group", group_id, str(msg))
"""

from dataclasses import dataclass, field
from typing import Union

from qingci_plugin_sdk.segments import to_v11_segment

# CQ 码值转义：& 与 , 转义后 OneBot 实现端解析时还原
_CQ_VALUE_TRANS = str.maketrans(
    {
        "&": "&amp;",
        ",": "&#44;",
        "[": "&#91;",
        "]": "&#93;",
    }
)


def cq_escape(value) -> str:
    """转义 CQ 码值中的特殊字符"""
    return str(value).translate(_CQ_VALUE_TRANS)


def _segment_to_cq(seg: dict) -> str:
    """将单条消息段 dict 序列化为 CQ 码（v11 平台专用）

    v12 段（mention/mention_all/voice 等）先经 SDK 转回 v11 段再序列化；
    text 段直接输出纯文本（不包装为 CQ 码）。
    """
    v11 = to_v11_segment(seg)
    seg_type = v11.get("type", "")
    data = v11.get("data", {})
    if not isinstance(data, dict):
        data = {}
    if seg_type == "text":
        return str(data.get("text", ""))
    parts = [f"[CQ:{seg_type}"]
    for key, value in data.items():
        if value is None or value == "":
            continue
        parts.append(f",{key}={cq_escape(value)}")
    parts.append("]")
    return "".join(parts)


def segments_to_cq(message: str | list) -> str:
    """将消息序列化为 OneBot 11 CQ 码字符串（v11 平台发送专用）

    入参可为：纯文本字符串 / v11 段数组 / v12 段数组。
    v12 段（mention -> at、mention_all -> at all、voice -> record）在
    序列化前自动转回 v11 段。
    """
    if isinstance(message, str):
        return message
    return "".join(_segment_to_cq(seg) for seg in message if isinstance(seg, dict))


@dataclass
class MessageSegment:
    """单个消息段：type + data，字符串化时转义为 CQ 码"""

    type: str
    data: dict = field(default_factory=dict)

    # ---- 工厂方法 ----

    @staticmethod
    def text(content: str) -> "MessageSegment":
        """纯文本段"""
        return MessageSegment("text", {"text": content})

    @staticmethod
    def at(qq: int | str) -> "MessageSegment":
        """@ 某人"""
        return MessageSegment("at", {"qq": qq})

    @staticmethod
    def at_all() -> "MessageSegment":
        """@ 全体成员"""
        return MessageSegment("at", {"qq": "all"})

    @staticmethod
    def image(file: str) -> "MessageSegment":
        """图片（file 可为 URL、本地路径或 base64）"""
        return MessageSegment("image", {"file": file})

    @staticmethod
    def face(face_id: int) -> "MessageSegment":
        """QQ 表情"""
        return MessageSegment("face", {"id": face_id})

    @staticmethod
    def voice(file: str) -> "MessageSegment":
        """语音消息（record 段）"""
        return MessageSegment("record", {"file": file})

    @staticmethod
    def video(file: str) -> "MessageSegment":
        """短视频"""
        return MessageSegment("video", {"file": file})

    @staticmethod
    def reply(message_id: str | int) -> "MessageSegment":
        """回复指定消息"""
        return MessageSegment("reply", {"id": message_id})

    @staticmethod
    def forward(forward_id: str) -> "MessageSegment":
        """合并转发消息"""
        return MessageSegment("forward", {"id": forward_id})

    def __str__(self) -> str:
        parts = [f"[CQ:{self.type}"]
        for key, value in self.data.items():
            if value is None or value == "":
                continue
            parts.append(f",{key}={cq_escape(value)}")
        parts.append("]")
        return "".join(parts)


class Message(list[MessageSegment]):
    """消息段列表：支持混合追加、字符串化、提取纯文本

    可直接 `str(message)` 得到 OneBot 可发送的 CQ 码字符串。
    """

    def __init__(self, *segments: Union[MessageSegment, str, "Message"]):
        super().__init__()
        for seg in segments:
            self.append(seg)

    def append(self, item) -> None:
        if isinstance(item, str):
            super().append(MessageSegment.text(item))
        elif isinstance(item, Message):
            for seg in item:
                super().append(seg)
        elif isinstance(item, (list, tuple)):
            # Message([seg1, seg2]) / Message((seg1, seg2)) 展平追加
            for seg in item:
                self.append(seg)
        elif isinstance(item, MessageSegment):
            super().append(item)
        else:
            raise TypeError(
                f"Message 只接受 MessageSegment / str / Message / 列表，收到 {type(item).__name__}"
            )

    def __str__(self) -> str:
        return "".join(str(seg) for seg in self)

    def extract_plain_text(self) -> str:
        """提取纯文本（仅 text 段内容拼接后 strip）"""
        return "".join(seg.data.get("text", "") for seg in self if seg.type == "text").strip()

    def get(self, seg_type: str) -> MessageSegment | None:
        """取第一个指定类型的段，不存在返回 None"""
        for seg in self:
            if seg.type == seg_type:
                return seg
        return None
