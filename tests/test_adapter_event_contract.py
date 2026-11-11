# Copyright (C) 2026 Zhou Zhe (aka luoqingci) and Qingci-Bot contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

"""P1 第二阶段：适配器边界的非阻断事件契约检查

``validate_v12_event`` 在 PlatformAdapter.emit_event 以「只告警、不阻断」
的方式落地：协议端实现差异大，缺字段的事件仍要原样转发给 Bot。
"""

import logging

import pytest

from bot.core.events import EventContractError, validate_v12_event
from bot.core.platforms.base import PlatformAdapter


def test_validate_v12_event_accepts_custom_required_subset():
    raw = {"type": "message", "detail_type": "private"}
    assert validate_v12_event(raw, required=("type", "detail_type")) is raw
    with pytest.raises(EventContractError, match="id"):
        validate_v12_event(raw, required=("type", "detail_type", "id"))


async def test_emit_event_injects_platform_and_dispatches():
    adapter = PlatformAdapter()
    received = []
    adapter.on_event(received.append)
    await adapter.emit_event({"type": "message", "detail_type": "private"})
    assert received == [{"type": "message", "detail_type": "private", "platform": "base"}]


async def test_incomplete_v12_event_warns_but_is_still_dispatched(caplog):
    adapter = PlatformAdapter()
    received = []
    adapter.on_event(received.append)
    with caplog.at_level(logging.WARNING, logger="qingci-bot.platforms"):
        await adapter.emit_event({"type": "message"})
    assert len(received) == 1
    assert received[0]["type"] == "message"
    assert "结构不完整" in caplog.text


async def test_complete_v12_event_does_not_warn(caplog):
    adapter = PlatformAdapter()
    adapter.on_event(lambda _event: None)
    with caplog.at_level(logging.WARNING, logger="qingci-bot.platforms"):
        await adapter.emit_event(
            {"type": "notice", "detail_type": "group_increase", "platform": "qq"}
        )
    assert "结构不完整" not in caplog.text


async def test_v11_style_event_skips_v12_validation(caplog):
    """无 type 的 v11 兼容事件不参与 v12 校验，避免误告警"""
    adapter = PlatformAdapter()
    received = []
    adapter.on_event(received.append)
    with caplog.at_level(logging.WARNING, logger="qingci-bot.platforms"):
        await adapter.emit_event({"post_type": "message"})
    assert received[0]["post_type"] == "message"
    assert "结构不完整" not in caplog.text
