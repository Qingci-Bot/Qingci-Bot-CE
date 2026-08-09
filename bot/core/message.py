"""类型化消息构造器 - 借鉴 NoneBot2 的 Message / MessageSegment 设计

提供类型化的消息段构造（text / at / image / face / voice / video /
reply / forward），统一 CQ 码转义，替代手写 CQ 码字符串拼接。

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
from typing import Optional, Union

# CQ 码值转义：& 与 , 转义后 OneBot 实现端解析时还原
_CQ_VALUE_TRANS = str.maketrans({
    "&": "&amp;",
    ",": "&#44;",
    "[": "&#91;",
    "]": "&#93;",
})


def cq_escape(value) -> str:
    """转义 CQ 码值中的特殊字符"""
    return str(value).translate(_CQ_VALUE_TRANS)


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
    def at(qq: Union[int, str]) -> "MessageSegment":
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
    def reply(message_id: Union[str, int]) -> "MessageSegment":
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


class Message(list):
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
        else:
            super().append(item)

    def __str__(self) -> str:
        return "".join(str(seg) for seg in self)

    def extract_plain_text(self) -> str:
        """提取纯文本（仅 text 段内容拼接后 strip）"""
        return "".join(
            seg.data.get("text", "") for seg in self if seg.type == "text"
        ).strip()

    def get(self, seg_type: str) -> Optional[MessageSegment]:
        """取第一个指定类型的段，不存在返回 None"""
        for seg in self:
            if seg.type == seg_type:
                return seg
        return None
