"""多模态配置测试

只覆盖配置层：默认关闭、字段解析、边界值。图片解析本身在 SDK 侧
（`qingci_plugin_sdk.media`）有 20 例测试，这里不重复。
"""

from __future__ import annotations

from bot.config import LLMConfig, MultimodalConfig


def test_multimodal_disabled_by_default():
    """默认必须关闭：纯文本模型收到图片内容会直接报错"""
    assert LLMConfig().multimodal.enabled is False


def test_multimodal_defaults():
    mm = MultimodalConfig()
    assert mm.max_images == 4
    assert mm.max_bytes == 4 * 1024 * 1024
    assert mm.timeout == 15.0


def test_multimodal_parsed_from_dict():
    cfg = LLMConfig(
        multimodal={"enabled": True, "max_images": 2, "max_bytes": 1024 * 1024, "timeout": 5}
    )
    assert cfg.multimodal.enabled is True
    assert cfg.multimodal.max_images == 2
    assert cfg.multimodal.max_bytes == 1024 * 1024
    assert cfg.multimodal.timeout == 5


def test_multimodal_unknown_fields_ignored():
    """配置里多写字段不应导致校验失败（与 LLMConfig 一致，extra=ignore）"""
    cfg = LLMConfig(multimodal={"enabled": True, "unknown_key": 1})
    assert cfg.multimodal.enabled is True


def test_llm_config_without_multimodal_still_valid():
    """老配置没有该字段时仍能加载，落到默认值"""
    cfg = LLMConfig(provider="deepseek", model="deepseek-chat")
    assert cfg.multimodal.enabled is False
