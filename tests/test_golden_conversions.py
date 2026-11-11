"""P0 黄金快照：消息段 / CQ 码 / v11→v12 事件翻译的行为锁定

表驱动断言当前实际输出。这些结果是插件、协议端与 SDK 之间的隐式契约：
后续「事件类型化」「段转换合并」等重构必须保持它们不变。
"""

import pytest
from qingci_plugin_sdk.segments import to_v11_segment

from bot.core.message import Message, MessageSegment, cq_escape, segments_to_cq
from bot.core.v11_compat import v11_event_to_v12


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a&b,c[d]", "a&amp;b&#44;c&#91;d&#93;"),
        ("普通文本", "普通文本"),
        ("", ""),
        (123, "123"),
    ],
)
def test_cq_escape_golden(raw, expected):
    assert cq_escape(raw) == expected


@pytest.mark.parametrize(
    "factory, args, expected",
    [
        ("text", ("hi",), "[CQ:text,text=hi]"),
        ("at", (10001,), "[CQ:at,qq=10001]"),
        ("at_all", (), "[CQ:at,qq=all]"),
        ("image", ("http://x?a=1&b=2",), "[CQ:image,file=http://x?a=1&amp;b=2]"),
        ("face", (1,), "[CQ:face,id=1]"),
        ("voice", ("a.mp3",), "[CQ:record,file=a.mp3]"),
        ("video", ("a.mp4",), "[CQ:video,file=a.mp4]"),
        ("reply", (123,), "[CQ:reply,id=123]"),
        ("forward", ("fid",), "[CQ:forward,id=fid]"),
    ],
)
def test_message_segment_factory_golden(factory, args, expected):
    assert str(getattr(MessageSegment, factory)(*args)) == expected


def test_message_segment_skips_empty_data():
    assert str(MessageSegment("at", {"qq": None, "x": ""})) == "[CQ:at]"


def test_message_flattens_and_extracts_text():
    msg = Message("纯文本", MessageSegment.at(10001), [MessageSegment.text("尾")])
    assert str(msg) == "[CQ:text,text=纯文本][CQ:at,qq=10001][CQ:text,text=尾]"
    assert msg.extract_plain_text() == "纯文本尾"
    assert msg.get("at") == MessageSegment("at", {"qq": 10001})
    assert msg.get("image") is None
    with pytest.raises(TypeError):
        Message(123)


@pytest.mark.parametrize(
    "payload, expected",
    [
        ("纯文本 & 原样", "纯文本 & 原样"),
        (
            [
                {"type": "text", "data": {"text": "你好"}},
                {"type": "at", "data": {"qq": 10001}},
            ],
            "你好[CQ:at,qq=10001]",
        ),
        ([{"type": "mention", "data": {"user_id": "10001"}}], "[CQ:at,qq=10001]"),
        ([{"type": "mention_all", "data": {}}], "[CQ:at,qq=all]"),
        ([{"type": "image", "data": {"file_id": "abc"}}], "[CQ:image,file=abc]"),
        ([{"type": "text", "data": {"text": "x&y"}}], "x&y"),
        ([{"type": "at", "data": {"qq": None}}], "[CQ:at]"),
        (["跳过非 dict", {"type": "text", "data": {"text": "保留"}}], "保留"),
    ],
)
def test_segments_to_cq_golden(payload, expected):
    assert segments_to_cq(payload) == expected


@pytest.mark.parametrize(
    "segment",
    [
        {"type": "mention", "data": {"user_id": "10001"}},
        {"type": "mention_all", "data": {}},
        {"type": "image", "data": {"file_id": "abc"}},
        {"type": "text", "data": {"text": "x&y"}},
        {"type": "at", "data": {"qq": 7}},
    ],
)
def test_cq_output_matches_sdk_v11_mapping(segment):
    """CE 的段序列化结果必须与 SDK 的 v12→v11 映射保持组合一致"""
    v11 = to_v11_segment(segment)
    data = v11.get("data", {})
    expected = (
        str(data.get("text", ""))
        if v11.get("type") == "text"
        else str(MessageSegment(v11["type"], data))
    )
    assert segments_to_cq([segment]) == expected


def test_meta_event_golden():
    raw = {
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
        "self_id": 1,
    }
    assert v11_event_to_v12(raw) == {
        "type": "meta",
        "detail_type": "heartbeat",
        "sub_type": "",
        "id": "",
        "impl": "onebot11",
        "platform": "onebot",
        "self_id": "1",
        "time": 0,
    }


def test_private_message_golden():
    raw = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": 1,
        "user_id": 2,
        "message": [{"type": "text", "data": {"text": "hi"}}],
        "raw_message": "hi",
        "self_id": 9,
    }
    assert v11_event_to_v12(raw) == {
        "type": "message",
        "detail_type": "private",
        "sub_type": "friend",
        "id": "1",
        "impl": "onebot11",
        "platform": "onebot",
        "self_id": "9",
        "time": 0,
        "message_id": "1",
        "message": [{"type": "text", "data": {"text": "hi"}}],
        "alt_message": "hi",
        "user_id": "2",
        "group_id": "",
        "sender": {},
    }


def test_unknown_event_passed_through_without_platform_default():
    raw = {"post_type": "something_else", "foo": "bar"}
    assert v11_event_to_v12(raw) == raw
