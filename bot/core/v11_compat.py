"""OneBot 11 事件 -> OneBot 12 事件 翻译层（方案 A 迁移 M3）

OneBot 11 作为"众多平台之一"接入时，由各 v11 适配器（aiocqhttp 的
OneBotConnection、以及尚未迁移的旧 telegram 归一化）产出 v11 事件
dict。本模块提供 v11 -> v12 事件翻译，使核心只消费 v12 事件模型。

协议映射单一来源：v11 <-> v12 的段/事件类型映射与翻译逻辑已收敛进
独立插件 SDK（`qingci_plugin_sdk.events.translate_v11_event`），本模块
为薄转发，仅补充平台特有字段（OneBot 11 的 impl / platform 默认值），
避免两处实现随时间漂移导致插件跨平台行为不一致。
"""

from __future__ import annotations

from typing import Any

from qingci_plugin_sdk.events import translate_v11_event


def v11_event_to_v12(event: dict) -> dict[str, Any]:
    """将 OneBot 11 事件 dict 翻译为 OneBot 12 事件 dict

    转发 SDK 的统一翻译，并补充 OneBot 11 平台字段：
    - impl 固定为 "onebot11"
    - platform 缺省时默认 "onebot"（与 OneBotConnection 主连接语义一致）

    无法识别的事件类型由 SDK 原样返回（防御性，不丢事件）。
    """
    v12: dict[str, Any] = translate_v11_event(event, impl="onebot11")
    # 仅对已识别的事件补充平台默认（未知事件原样透传，保持兼容）
    if event.get("post_type") in ("message", "notice", "request", "meta_event"):
        if not v12.get("platform"):
            v12["platform"] = "onebot"
    return v12
