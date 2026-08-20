"""敏感信息日志脱敏

异常消息中可能携带完整 URL（如 Gemini 的 ``?key=`` 查询参数）或
Authorization 头（``Bearer ...``）。统一在写日志前脱敏，避免 API Key 落盘。
"""

from __future__ import annotations

import re

# 形如 key=xxx / token=xxx / api_key=xxx / apikey=xxx 的 URL 查询参数
_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:key|token|secret|password|api_key|apikey)=)[^&\s\"']+")
# Authorization: Bearer xxx
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
# 裸的 sk- 前缀密钥（OpenAI 风格）
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_]{8,}")


def redact_secrets(text: str) -> str:
    """将文本中的疑似密钥替换为 ***"""
    if not text:
        return text
    text = _QUERY_SECRET_RE.sub(r"\1***", text)
    text = _BEARER_RE.sub(r"\1***", text)
    text = _SK_RE.sub("sk-***", text)
    return text
