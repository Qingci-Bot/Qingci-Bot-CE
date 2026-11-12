# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""事件契约层（P1）：OneBot 12 / OneBot 11 事件的结构化类型与只读视图

边界（改动前先读这里）：

- SDK 的 ``MessageContext.from_v12_event`` 以及插件的 ``Permission.check`` /
  ``Rule.check`` 都以 **dict** 接收事件；插件 API 由 Plugins-SDK 定义，
  主项目不能把事件换成自定义对象，否则内置插件与外部插件会走两条路径。
- 因此本模块只提供 TypedDict 契约 + 只读视图：运行时事件仍是同一个 dict，
  零拷贝、零行为变化，``EventView.raw`` 与传入对象恒等。
- 新代码用视图的属性访问（各字段默认值集中在这里，避免 dispatcher 与
  适配器各写一份 ``event.get(key, default)``），存量代码继续用 ``.get()``；
  两条路径可长期共存，后续逐步迁移。

事件是双模输入（见 ``MessageDispatcher.dispatch``）：v12 事件含 ``type``，
v11 事件含 ``post_type``。``V12Event`` / ``V11Event`` 用 ``total=False``
描述真实载荷 —— 平台实现端字段经常缺失或类型不一致，强制必填会误伤。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict, cast

from ..errors import AppError

__all__ = [
    "EventContractError",
    "EventView",
    "PlatformEvent",
    "V11Event",
    "V12Event",
    "validate_v12_event",
]


class V12Event(TypedDict, total=False):
    """OneBot 12 内部事件（适配器归一化后的统一模型）"""

    type: str
    detail_type: str
    sub_type: str
    id: str
    impl: str
    platform: str
    self_id: str
    time: float
    message_id: str
    message: list[dict[str, Any]]
    alt_message: str
    user_id: str
    group_id: str
    sender: dict[str, Any]


class V11Event(TypedDict, total=False):
    """OneBot 11 原始事件（兼容路径，字段类型由实现端决定）"""

    post_type: str
    message_type: str
    sub_type: str
    message_id: int | str
    user_id: int | str
    group_id: int | str
    self_id: int | str
    raw_message: str
    message: str | list[dict[str, Any]]
    sender: dict[str, Any]
    platform: str
    time: int


PlatformEvent = V12Event | V11Event

_REQUIRED_V12_FIELDS = ("type", "detail_type", "id", "impl", "platform", "self_id", "time")
_V12_TYPE_TO_POST = {
    "message": "message",
    "notice": "notice",
    "request": "request",
    "meta": "meta_event",
}


class EventContractError(AppError, ValueError):
    """事件结构不符合契约（供适配器边界与测试使用，不在主链路强制开启）"""

    code = "event_contract_error"


def validate_v12_event(
    raw: Mapping[str, Any], *, required: tuple[str, ...] = _REQUIRED_V12_FIELDS
) -> V12Event:
    """校验 v12 事件字段；只校验不改写，返回同一对象（仅类型层 cast）

    ``required`` 可缩小到核心字段（适配器边界的非阻断检查只要求
    ``type`` / ``detail_type``），完整校验留给契约测试与新增适配器自检。
    未知事件允许原样透传，不在主链路强制开启完整校验。
    """
    missing = [key for key in required if key not in raw]
    if missing:
        raise EventContractError(f"v12 事件缺少必需字段: {', '.join(missing)}")
    return cast(V12Event, raw)


class EventView:
    """只读事件视图：统一字段默认值，不复制也不改写原始 dict

    默认值与原 ``MessageDispatcher`` 里的 ``event.get(key, default)`` 逐一
    对齐（含 ``sender`` 的 ``or {}``、``platform`` 的 ``str(...) or``），
    因此替换读取方式不改变任何运行时结果。
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self._raw = raw

    def _field(self, key: str, default: str = "") -> str:
        """读取字符串字段；cast 仅在类型层，返回原始值（含 None / 非 str）

        事件字段由平台实现端决定，强行 ``str()`` 会把 None 变成 "None"，
        因此这里只做类型层转换，保持与 ``event.get(key, default)`` 等价。
        """
        return cast(str, self._raw.get(key, default))

    @property
    def raw(self) -> Mapping[str, Any]:
        """原始事件对象（与构造时传入的恒等）"""
        return self._raw

    @property
    def is_v12(self) -> bool:
        return bool(self._raw.get("type"))

    @property
    def event_type(self) -> str:
        return self._field("type")

    @property
    def detail_type(self) -> str:
        return self._field("detail_type")

    @property
    def post_type(self) -> str:
        """v11 的 post_type 原值；v12 事件无此字段时为空串"""
        return self._field("post_type")

    @property
    def derived_post_type(self) -> str:
        """由 v12 的 type 派生 post_type（新代码可用，不改变既有路径）"""
        if self.post_type:
            return self.post_type
        return _V12_TYPE_TO_POST.get(str(self._raw.get("type") or ""), "")

    @property
    def message_type(self) -> str:
        return self._field("message_type")

    @property
    def sub_type(self) -> str:
        return self._field("sub_type")

    @property
    def message_id(self) -> Any:
        return self._raw.get("message_id")

    @property
    def self_id(self) -> Any:
        return self._raw.get("self_id", 0)

    @property
    def user_id(self) -> Any:
        return self._raw.get("user_id", 0)

    @property
    def group_id(self) -> Any:
        return self._raw.get("group_id", 0)

    @property
    def sender(self) -> dict[str, Any]:
        return self._raw.get("sender") or {}

    @property
    def raw_message(self) -> str:
        return self._field("raw_message")

    @property
    def message(self) -> Any:
        return self._raw.get("message")

    @property
    def platform(self) -> str:
        return str(self._raw.get("platform", "onebot") or "onebot")

    @property
    def event_id(self) -> str:
        return self._field("id")

    @property
    def impl(self) -> str:
        return self._field("impl")

    @property
    def time(self) -> Any:
        return self._raw.get("time", 0)
