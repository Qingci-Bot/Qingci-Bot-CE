"""配置管理模块 - 基于 YAML 的配置读写"""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


# ============ 配置模型 ============

class OneBotConfig(BaseModel):
    """OneBot WebSocket 连接配置"""
    model_config = {"extra": "ignore"}

    host: str = "127.0.0.1"
    port: int = 3001
    access_token: str = ""


class LLMConfig(BaseModel):
    """LLM 大模型配置"""
    model_config = {"extra": "ignore"}

    provider: str = "openai"           # openai / deepseek / ollama
    api_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    max_tokens: int = 2048
    temperature: float = 0.7
    system_prompt: str = "你是一个友好的 QQ 机器人助手。请用简洁、自然的中文回复。"
    max_history: int = 20               # 最大对话历史轮数


class BotConfig(BaseModel):
    """Bot 基础配置"""
    model_config = {"extra": "ignore"}

    name: str = "Qingci-Bot"
    admin_users: list[int] = []         # 管理员 QQ 号
    trigger_mode: str = "at"            # 触发方式: at / keyword / always
    trigger_keywords: list[str] = ["/bot", "/ai"]
    group_blacklist: list[int] = []     # 群黑名单
    user_blacklist: list[int] = []      # 用户黑名单


class AppConfig(BaseModel):
    """应用总配置"""
    model_config = {"extra": "ignore"}

    bot: BotConfig = BotConfig()
    onebot: OneBotConfig = OneBotConfig()
    llm: LLMConfig = LLMConfig()
    api_key: str = ""               # API 鉴权密钥，为空则不启用鉴权


# ============ 配置管理器 ============

DEFAULT_CONFIG_PATH = Path("config.yaml")


class ConfigManager:
    """配置管理器：加载、保存、热更新"""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DEFAULT_CONFIG_PATH
        self._config: AppConfig = AppConfig()

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def bot(self) -> BotConfig:
        return self._config.bot

    @property
    def onebot(self) -> OneBotConfig:
        return self._config.onebot

    @property
    def llm(self) -> LLMConfig:
        return self._config.llm

    def load(self) -> AppConfig:
        """从文件加载配置，不存在则创建默认配置"""
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._config = AppConfig(**data)
        else:
            self.save()
        return self._config

    def save(self):
        """保存配置到文件"""
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(
                self._config.model_dump(),
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

    def update(self, data: dict):
        """更新配置并保存"""
        self._config = AppConfig(**data)
        self.save()

    def reload(self) -> AppConfig:
        """重新加载配置"""
        return self.load()

    def to_dict(self) -> dict:
        return self._config.model_dump()