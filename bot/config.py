"""配置管理模块 - 基于 YAML 的配置读写"""

import logging
import threading
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ValidationError, model_validator


# ============ 配置模型 ============

class OneBotConfig(BaseModel):
    """OneBot WebSocket 连接配置"""
    model_config = {"extra": "ignore"}

    host: str = "127.0.0.1"
    port: int = 3001
    access_token: str = ""


# 提供商预设：切换 provider 时自动带出推荐的 api_url 和 model
LLM_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {"api_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "deepseek": {"api_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "ollama": {"api_url": "http://localhost:11434", "model": "llama3.1"},
    "siliconflow": {"api_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    "claude": {"api_url": "https://api.anthropic.com/v1", "model": "claude-3-5-sonnet-20241022"},
    "gemini": {"api_url": "https://generativelanguage.googleapis.com/v1", "model": "gemini-1.5-flash"},
    "custom": {"api_url": "", "model": "gpt-4o-mini"},
}


class LLMConfig(BaseModel):
    """LLM 大模型配置"""
    model_config = {"extra": "ignore"}

    provider: str = "openai"           # openai / deepseek / ollama / 自定义 litellm provider
    api_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    max_tokens: int = 2048             # 单次回复最大 token
    temperature: float = 0.7
    system_prompt: str = "你是一个友好的 QQ 机器人助手。请用简洁、自然的中文回复。"
    max_history: int = 20               # 最大对话历史轮数（每轮 = user + assistant）
    max_context_tokens: int = 8192      # 上下文窗口 token 上限，超出后裁剪历史

    @model_validator(mode="after")
    def apply_provider_preset(self):
        """根据 provider 自动切换 api_url 和 model。

        仅当当前 api_url/model 为空或与某个 preset 一致时才会更新，
        避免覆盖用户自定义的地址和模型。
        """
        preset = LLM_PROVIDER_PRESETS.get(self.provider)
        if not preset:
            return self

        preset_api_urls = {p["api_url"] for p in LLM_PROVIDER_PRESETS.values() if p["api_url"]}
        preset_models = {p["model"] for p in LLM_PROVIDER_PRESETS.values()}

        if not self.api_url or self.api_url in preset_api_urls:
            self.api_url = preset["api_url"]
        if not self.model or self.model in preset_models:
            self.model = preset["model"]
        return self


class BotConfig(BaseModel):
    """Bot 基础配置"""
    model_config = {"extra": "ignore"}

    name: str = "Qingci-Bot"
    admin_users: list[int] = []         # 管理员 QQ 号
    trigger_mode: Literal["at", "keyword", "always"] = "at"  # 触发方式
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

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class ConfigManager:
    """配置管理器：加载、保存、热更新"""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DEFAULT_CONFIG_PATH
        self._config: AppConfig = AppConfig()
        self._lock = threading.RLock()

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
        with self._lock:
            if self._path.exists():
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    self._config = AppConfig(**data)
                except yaml.YAMLError as e:
                    logger = logging.getLogger("qingci-bot.config")
                    logger.error(f"配置文件 YAML 格式错误: {e}")
                    self._config = AppConfig()
                    self.save()
                except ValidationError as e:
                    logger = logging.getLogger("qingci-bot.config")
                    logger.error(f"配置字段验证失败: {e}")
                    self._config = AppConfig()
                    self.save()
                except Exception as e:
                    logger = logging.getLogger("qingci-bot.config")
                    logger.error(f"配置文件解析失败，使用默认配置: {e}")
                    self._config = AppConfig()
                    self.save()
            else:
                self.save()
            return self._config

    def save(self):
        """保存配置到文件（原子写入）"""
        with self._lock:
            import os
            import tempfile
            data = self._config.model_dump()
            # 先写入临时文件，再原子重命名
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    yaml.dump(
                        data,
                        f,
                        allow_unicode=True,
                        default_flow_style=False,
                        sort_keys=False,
                    )
                os.replace(tmp_path, str(self._path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def update(self, data: dict):
        """更新配置并保存（save 失败时回滚内存）"""
        with self._lock:
            new_config = AppConfig(**data)
            old_config = self._config
            self._config = new_config
            try:
                self.save()
            except Exception:
                self._config = old_config
                raise

    def reload(self) -> AppConfig:
        """重新加载配置"""
        return self.load()

    def to_dict(self) -> dict:
        return self._config.model_dump()