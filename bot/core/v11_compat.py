"""OneBot 11 事件 -> OneBot 12 事件 翻译层（方案 A 迁移 M3）

OneBot 11 作为"众多平台之一"接入时，由各 v11 适配器（aiocqhttp 的
OneBotConnection、以及尚未迁移的旧 telegram 归一化）产出 v11 事件
dict。本模块提供纯函数翻译：v11 事件 -> v12 事件，使核心只消费
v12 事件模型（type / detail_type / message[]），v11 平台对核心透明。

翻译要点：
- message:  post_type -> type；message_type -> detail_type；
           raw_message -> alt_message；ID 字段字符串化
- notice:   notice_type -> detail_type（group_admin / group_ban 按
           sub_type 细分；映射关系与 SDK events 模块保持对称）
- request:  request_type -> detail_type
- meta:     meta_event_type -> detail_type
"""

from __future__ import annotations

import re
from typing import Any

# v11 CQ 码 -> v12 段类型映射（data 字段名同步归一化）
_CQ_TYPE_TO_V12: dict[str, str] = {
    "at": "mention",
    "face": "face",
    "image": "image",
    "record": "voice",
    "video": "video",
    "reply": "reply",
    "forward": "forward",
}
_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_]+)((?:,[a-zA-Z0-9_]+=[^,\]]*)*)\]")


def _parse_cq_string(text: str) -> list[dict[str, Any]]:
    """把含 CQ 码的 v11 字符串消息解析为 v12 段数组（纯文本保持 text 段）"""
    segments: list[dict[str, Any]] = []
    pos = 0
    for m in _CQ_RE.finditer(text):
        if m.start() > pos:
            segments.append({"type": "text", "data": {"text": text[pos : m.start()]}})
        cq_type = m.group(1)
        data: dict[str, str] = {}
        raw_kvs = m.group(2)
        if raw_kvs:
            for kv in raw_kvs.lstrip(",").split(","):
                key, _, value = kv.partition("=")
                data[key.strip()] = value
        # 段类型与字段归一化到 v12 命名空间
        v12_type = _CQ_TYPE_TO_V12.get(cq_type, cq_type)
        if v12_type == "mention":
            if data.get("qq") == "all":
                v12_type = "mention_all"
                data = {}
            else:
                data = {"user_id": data.get("qq", "")}
        elif v12_type == "reply":
            # v11 reply 用 data.id，v12 用 data.message_id
            data = {"message_id": data.get("id", "")}
        elif v12_type == "image":
            data = {"file_id": data.get("file", "")}
        elif v12_type in ("face", "voice", "video"):
            data = {"file_id": data.get("file", "") or data.get("id", "")}
        elif v12_type == "text":
            data = {"text": data.get("text", "")}
        segments.append({"type": v12_type, "data": data})
        pos = m.end()
    if pos < len(text):
        segments.append({"type": "text", "data": {"text": text[pos:]}})
    return segments


# v11 notice_type -> v12 detail_type（固定映射）
_NOTICE_TYPE_TO_DETAIL: dict[str, str] = {
    "friend_recall": "private_message_delete",
    "friend_add": "friend_increase",
    "group_increase": "group_member_increase",
    "group_decrease": "group_member_decrease",
    "group_recall": "group_message_delete",
    "group_upload": "group_file_upload",
    "poke": "group_poke",
    # 以下按 sub_type 细分，见 _notice_detail_type()
    "group_admin": "",
    "group_ban": "",
}


def _notice_detail_type(notice_type: str, sub_type: str) -> str:
    """v11 notice_type + sub_type -> v12 detail_type（含细分场景）"""
    if notice_type == "group_admin":
        return "group_admin_set" if sub_type == "set" else "group_admin_unset"
    if notice_type == "group_ban":
        return "group_member_unban" if sub_type == "lift_ban" else "group_member_ban"
    return _NOTICE_TYPE_TO_DETAIL.get(notice_type, notice_type)


def _base_fields(event: dict, event_type: str, detail_type: str) -> dict[str, Any]:
    """v12 事件基础字段（type / detail_type / id / platform / self_id / time）"""
    return {
        "type": event_type,
        "detail_type": detail_type,
        "sub_type": event.get("sub_type", ""),
        "id": str(event.get("message_id") or event.get("flag") or ""),
        "impl": "onebot11",
        "platform": str(event.get("platform", "") or "onebot"),
        "self_id": str(event.get("self_id", "") or ""),
        "time": event.get("time", 0),
    }


def _v11_message_to_v12(event: dict) -> dict[str, Any]:
    v12 = _base_fields(event, "message", str(event.get("message_type", "")))
    # v11 message 可能是字符串（含 CQ 码）或段数组：
    # 字符串含 CQ 码时解析为 v12 段数组（否则 @bot 的 CQ:at 会被整体包成
    # text 段，导致 is_at_bot / at_list 失真、@bot /cmd 不触发）
    raw_message = event.get("message", [])
    if isinstance(raw_message, str) and "CQ:" in raw_message:
        raw_message = _parse_cq_string(raw_message)
    v12.update(
        {
            "message_id": str(event.get("message_id", "") or ""),
            "message": raw_message,
            "alt_message": str(event.get("raw_message", "") or ""),
            "user_id": str(event.get("user_id", "") or ""),
            "group_id": str(event.get("group_id", "") or ""),
            "sender": event.get("sender", {}) or {},
        }
    )
    return v12


def _v11_notice_to_v12(event: dict) -> dict[str, Any]:
    notice_type = str(event.get("notice_type", ""))
    detail_type = _notice_detail_type(notice_type, str(event.get("sub_type", "")))
    v12 = _base_fields(event, "notice", detail_type)
    v12.update(
        {
            "user_id": str(event.get("user_id", "") or ""),
            "group_id": str(event.get("group_id", "") or ""),
            "operator_id": str(event.get("operator_id", "") or ""),
        }
    )
    # 携带 v11 原始通知字段，供 LLM 事件缓冲等读取
    for key in ("duration", "target_id", "file", "message_id"):
        if key in event:
            v12[key] = event[key]
    return v12


def _v11_request_to_v12(event: dict) -> dict[str, Any]:
    v12 = _base_fields(event, "request", str(event.get("request_type", "")))
    v12.update(
        {
            "user_id": str(event.get("user_id", "") or ""),
            "group_id": str(event.get("group_id", "") or ""),
            "comment": str(event.get("comment", "") or ""),
            "flag": str(event.get("flag", "") or ""),
        }
    )
    return v12


def _v11_meta_to_v12(event: dict) -> dict[str, Any]:
    v12 = _base_fields(event, "meta", str(event.get("meta_event_type", "")))
    v12.update({"sub_type": str(event.get("sub_type", ""))})
    if "status" in event:
        v12["status"] = event["status"]
    return v12


def v11_event_to_v12(event: dict) -> dict[str, Any]:
    """将 OneBot 11 事件 dict 翻译为 OneBot 12 事件 dict

    无法识别的事件类型原样返回（防御性，不丢事件）。
    """
    post_type = event.get("post_type", "")
    if post_type == "message":
        return _v11_message_to_v12(event)
    if post_type == "notice":
        return _v11_notice_to_v12(event)
    if post_type == "request":
        return _v11_request_to_v12(event)
    if post_type == "meta_event":
        return _v11_meta_to_v12(event)
    return dict(event)
