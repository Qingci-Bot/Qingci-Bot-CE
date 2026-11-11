# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

"""P1 事件契约层：只读视图、v12 结构校验与 dispatcher 行为回归

事件仍是同一个 dict（插件 API 由 SDK 定义），本组用例同时守住两件事：
视图的默认值与原 ``event.get(key, default)`` 完全一致，以及翻译后的
v12 事件满足契约。
"""

import pytest

from bot.core.dispatcher import MessageDispatcher
from bot.core.events import EventContractError, EventView, validate_v12_event
from bot.core.v11_compat import v11_event_to_v12

_COMPLETE_V12 = {
    "type": "message",
    "detail_type": "private",
    "sub_type": "friend",
    "id": "1",
    "impl": "onebot11",
    "platform": "onebot",
    "self_id": "9",
    "time": 0,
    "message_id": "7",
    "message": [{"type": "text", "data": {"text": "hi"}}],
    "alt_message": "hi",
    "user_id": "2",
    "group_id": "",
    "sender": {},
}


def test_view_keeps_raw_identity():
    raw = {"post_type": "message", "user_id": 1}
    view = EventView(raw)
    assert view.raw is raw
    assert view.post_type == "message"
    assert view.user_id == 1
    assert view.is_v12 is False


def test_v11_defaults_match_original_get_semantics():
    raw = {"post_type": "message", "sender": None, "platform": ""}
    view = EventView(raw)
    assert view.message_type == raw.get("message_type", "")
    assert view.sub_type == raw.get("sub_type", "")
    assert view.message_id is None
    assert view.self_id == 0
    assert view.user_id == 0
    assert view.group_id == 0
    assert view.sender == {}
    assert view.raw_message == ""
    assert view.message is None
    assert view.platform == "onebot"
    assert view.event_id == ""
    assert view.impl == ""
    assert view.time == 0
    assert view.detail_type == ""
    assert view.event_type == ""
    assert view.derived_post_type == "message"


@pytest.mark.parametrize(
    "event_type, post_type",
    [
        ("message", "message"),
        ("notice", "notice"),
        ("request", "request"),
        ("meta", "meta_event"),
        ("unknown", ""),
    ],
)
def test_derived_post_type(event_type, post_type):
    assert EventView({"type": event_type}).derived_post_type == post_type


def test_v12_view_flags_and_platform():
    view = EventView(_COMPLETE_V12)
    assert view.is_v12 is True
    assert view.post_type == ""
    assert view.derived_post_type == "message"
    assert view.platform == "onebot"
    assert view.event_id == "1"
    assert view.impl == "onebot11"
    assert view.detail_type == "private"
    assert view.message == [{"type": "text", "data": {"text": "hi"}}]


def test_validate_v12_event_accepts_complete_payload_without_copying():
    raw = dict(_COMPLETE_V12)
    assert validate_v12_event(raw) is raw


@pytest.mark.parametrize(
    "missing", ["type", "detail_type", "id", "impl", "platform", "self_id", "time"]
)
def test_validate_v12_event_rejects_missing_required_field(missing):
    raw = dict(_COMPLETE_V12)
    del raw[missing]
    with pytest.raises(EventContractError, match=missing):
        validate_v12_event(raw)


def test_translated_v11_event_satisfies_v12_contract():
    """P0 黄金快照里的样本必须仍满足 v12 结构契约"""
    v12 = v11_event_to_v12(
        {
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "message_id": 1,
            "user_id": 2,
            "message": [{"type": "text", "data": {"text": "hi"}}],
            "raw_message": "hi",
            "self_id": 9,
        }
    )
    assert validate_v12_event(v12) is v12
    assert v12["type"] == "message"


def test_dispatcher_v11_path_unchanged():
    raw = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": 7,
        "user_id": 2,
        "self_id": 9,
        "raw_message": "hi",
        "message": [{"type": "text", "data": {"text": "hi"}}],
    }
    ctx = MessageDispatcher().dispatch(raw)
    assert ctx.post_type == "message"
    assert ctx.message_type == "private"
    assert ctx.user_id == 2
    assert ctx.self_id == 9
    assert ctx.message_id == "7"
    assert ctx.plain_text == "hi"
    assert ctx.platform == "onebot"


def test_dispatcher_v12_path_unchanged():
    ctx = MessageDispatcher().dispatch(dict(_COMPLETE_V12))
    assert ctx.post_type == "message"
    assert ctx.plain_text == "hi"
    assert ctx.platform == "onebot"
