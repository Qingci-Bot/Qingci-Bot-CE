"""配置管理模块 - 基于 YAML 的配置读写"""

import logging
import threading
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, model_validator

from .paths import app_root


def _deep_merge(base: dict, patch: dict) -> dict:
    """递归合并 patch 到 base（返回新 dict，不修改入参）

    嵌套 dict 递归合并；非 dict 值（含 list）整体替换。
    """
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ============ 配置模型 ============


class OneBotConfig(BaseModel):
    """OneBot WebSocket 连接配置"""

    model_config = {"extra": "ignore"}

    enabled: bool = True  # 是否启动反向 WS 服务端（Telegram 等主平台实例可关闭）
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
    "gemini": {
        "api_url": "https://generativelanguage.googleapis.com/v1",
        "model": "gemini-1.5-flash",
    },
    "custom": {"api_url": "", "model": "gpt-4o-mini"},
}


class MCPConfig(BaseModel):
    """MCP (Model Context Protocol) 服务器配置

    command 非空时使用 stdio 传输（本地子进程，如 npx/uvx/python）；
    否则使用 url 走 HTTP 传输（远程 MCP 服务）。
    """

    model_config = {"extra": "ignore"}

    name: str = ""  # 服务器名（工具名将带 mcp_{name}_ 前缀）
    command: str = ""  # stdio 模式：子进程命令
    args: list[str] = []  # stdio 模式：命令参数
    url: str = ""  # HTTP 模式：MCP 服务地址
    env: dict[str, str] = {}  # 可选额外环境变量


class PersonaConfig(BaseModel):
    """人格配置：一组可切换的 system_prompt

    支持在聊天中通过 /persona 命令切换（会话级覆盖），
    也可在 WebUI 中管理并设置默认人格。
    """

    model_config = {"extra": "ignore"}

    name: str = ""  # 人格名（/persona <name> 切换）
    description: str = ""  # 简述（/persona 列表 时展示）
    system_prompt: str = ""  # 该人格的系统提示词


class LLMConfig(BaseModel):
    """LLM 大模型配置"""

    model_config = {"extra": "ignore"}

    provider: str = "openai"  # openai / deepseek / ollama / 自定义 litellm provider
    api_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    max_tokens: int = 2048  # 单次回复最大 token
    temperature: float = 0.7
    system_prompt: str = "你是一个友好、乐于助人的机器人助手。请用简洁、自然的中文回复。"
    # 人格列表与默认人格（default_persona 为空时使用 system_prompt）
    personas: list[PersonaConfig] = []
    default_persona: str = ""
    max_history: int = 20  # 最大对话历史轮数（每轮 = user + assistant）
    max_context_tokens: int = 8192  # 上下文窗口 token 上限，超出后裁剪历史
    timeout: int = 60  # 单次 LLM 请求超时（秒）
    num_retries: int = 2  # LLM 请求失败重试次数
    # 会话历史超长时生成摘要（默认关闭）。
    # 与 session_summary.enabled 等价：两者任一为 true 即启用摘要，
    # 阈值类参数统一读 session_summary 节（未配置时用默认值）。
    # 保留本字段是为了兼容已有 config.yaml，不要删除。
    enable_summary: bool = False
    enable_tools: bool = False  # Function Calling 工具调用（默认关闭）
    max_tool_rounds: int = 5  # 工具调用最大轮次
    mcp_servers: list[MCPConfig] = []  # MCP 服务器列表（enable_tools 开启后生效）

    @model_validator(mode="after")
    def apply_provider_preset(self):
        """根据 provider 自动切换 api_url 和 model。

        仅当当前 api_url/model 为空或等于某个已知预设值时
        才更新，避免覆盖用户自定义的地址和模型。
        custom 提供商完全由用户管理，不自动改动。
        """
        preset = LLM_PROVIDER_PRESETS.get(self.provider)
        if not preset or self.provider == "custom":
            return self

        # 判断 api_url 是否为某个已知预设值（即用户未自定义）
        is_default_url = not self.api_url or any(
            p["api_url"] == self.api_url for p in LLM_PROVIDER_PRESETS.values() if p["api_url"]
        )
        if is_default_url:
            self.api_url = preset["api_url"]

        # 判断 model 是否为某个已知预设值（即用户未自定义）
        is_default_model = not self.model or any(
            p["model"] == self.model for p in LLM_PROVIDER_PRESETS.values() if p["model"]
        )
        if is_default_model:
            self.model = preset["model"]

        return self


class BotConfig(BaseModel):
    """Bot 基础配置"""

    model_config = {"extra": "ignore"}

    name: str = "Qingci-Bot CE"
    super_admin: str | None = (
        None  # 超级管理员 ID（唯一；平台无关字符串标识，如 QQ 号 / Telegram 用户 ID）
    )
    admin_users: list[str] = []  # 普通管理员 ID 列表
    trigger_mode: Literal["at", "keyword", "always"] = "at"  # 触发方式
    trigger_keywords: list[str] = ["/bot", "/ai"]
    group_blacklist: list[str] = []  # 群黑名单（平台无关字符串标识）
    user_blacklist: list[str] = []  # 用户黑名单
    log_json: bool = False  # 结构化 JSON 日志（默认关闭，使用普通文本日志）
    wizard_skipped: bool = False  # 是否跳过了首次配置引导
    # 自动安装外部插件声明的第三方依赖到实例隔离的 deps 目录（data_root()/deps）。
    # 关闭可避免插件 requirements.txt 触发任意包安装（供给链风险），
    # 但依赖缺失时插件会加载失败。
    auto_install_plugin_deps: bool = True

    # admin_set 缓存：super_admin + admin_users 的并集集合（权限检查 O(1) 成员判断）
    _admin_set_cache: frozenset[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_numeric_ids_to_str(cls, data):
        """兼容旧配置：把数字 ID（int）统一为平台无关的字符串标识

        OneBot 12 事件里 user_id / group_id 均为字符串；旧 config.yaml
        中以 QQ 号（int）书写的管理员/黑白名单需在此归一为 str，避免
        因类型不符导致配置校验失败回退为默认配置（损坏备份）。
        """
        if isinstance(data, dict):
            v = data.get("super_admin")
            if isinstance(v, int):
                data["super_admin"] = str(v)
            for key in ("admin_users", "group_blacklist", "user_blacklist"):
                items = data.get(key)
                if isinstance(items, list):
                    data[key] = [str(x) for x in items if x is not None]
        return data

    @model_validator(mode="after")
    def _invalidate_admin_set(self):
        # 配置经 update/reload 重建实例时刷新缓存，避免在旧实例上残留
        self._admin_set_cache = None
        return self

    @property
    def admin_set(self) -> frozenset[str]:
        """超级管理员 + 普通管理员的并集集合（仅读，权限检查用）

        预编译为 frozenset，将权限检查从 O(n) 列表成员判断降为 O(1)；
        超级管理员自动包含在集合内（继承普通管理员权限）。
        """
        if self._admin_set_cache is None:
            members = set(self.admin_users or [])
            if self.super_admin is not None:
                members.add(self.super_admin)
            self._admin_set_cache = frozenset(members)
        return self._admin_set_cache


class RateLimitConfig(BaseModel):
    """对话限流配置（默认关闭）"""

    model_config = {"extra": "ignore"}

    enabled: bool = False
    daily_limit: int = 50  # 每用户每日对话上限
    cooldown_seconds: int = 10  # 同一用户两次对话最小间隔（秒）


class FilterConfig(BaseModel):
    """敏感词过滤配置（默认关闭）"""

    model_config = {"extra": "ignore"}

    enabled: bool = False
    words_file: str = "data/sensitive_words.txt"  # 词库文件路径（相对项目根目录）
    exempt_admins: bool = True  # 管理员豁免


class SchedulerConfig(BaseModel):
    """定时任务调度器配置（默认启用，插件未注册任务时无副作用）"""

    model_config = {"extra": "ignore"}

    enabled: bool = True


class HotReloadConfig(BaseModel):
    """插件开发期自动热重载配置（默认关闭）

    开启后定时轮询外部插件目录（plugins/）文件变更，开发期改代码自动重载，
    提升迭代效率。生产环境建议关闭。
    """

    model_config = {"extra": "ignore"}

    enabled: bool = False
    interval: float = 2.0  # 轮询间隔（秒）


class AlertConfig(BaseModel):
    """错误告警配置（默认关闭）"""

    model_config = {"extra": "ignore"}

    enabled: bool = False
    error_threshold: int = 5  # 冷却窗口内 ERROR 日志条数阈值
    cooldown_minutes: int = 10  # 告警冷却时间（分钟）


class ImageConfig(BaseModel):
    """图片生成配置（默认关闭）"""

    model_config = {"extra": "ignore"}

    enabled: bool = False
    model: str = "dall-e-3"
    api_url: str = ""  # 留空则按 litellm 默认路由
    api_key: str = ""


class RenderConfig(BaseModel):
    """HTML → 图片渲染服务配置（可选能力）

    基于 Playwright 无头 Chromium 将 HTML 渲染为 JPEG/PNG，供签到卡等
    插件复用。playwright 为可选依赖（`pyproject.toml` 的 `[render]` 分组）；
    未安装/浏览器缺失时渲染不可用，`render_html()` 抛
    HtmlRenderUnavailableError，调用方回退，框架启动不受影响。
    """

    model_config = {"extra": "ignore"}

    enabled: bool = True  # 渲染服务开关（关闭后渲染直接不可用）
    timeout: float = 30.0  # 单次渲染超时（秒）
    format: Literal["jpeg", "png"] = "jpeg"  # 默认输出格式
    quality: int = 92  # JPEG 质量（1-100；png 忽略）
    default_width: int = 800  # 默认渲染宽度（调用方未指定时）
    default_height: int = 600  # 默认渲染高度
    device_scale_factor: float = 1.0  # 输出清晰度倍率（如 2.0 对应 2x 高清）


class SessionSummaryConfig(BaseModel):
    """会话摘要（历史裁剪）配置（默认关闭）

    开启后，当会话上下文超过条数/token 阈值时，将较早消息异步
    摘要压缩为一条 summary 消息，保留最近 N 轮原文。
    开关与 llm.enable_summary 等价：两者任一为 true 即启用。
    """

    model_config = {"extra": "ignore"}

    enabled: bool = False
    keep_recent_turns: int = 3  # 摘要时保留最近 N 轮原文（每轮 = user + assistant）
    max_messages: int = 20  # 触发摘要的条数阈值（历史消息数超过即摘要）
    max_tokens: int = 4096  # 触发摘要的 token 阈值（估算超过即摘要）
    summary_max_tokens: int = 512  # 摘要生成单次回复最大 token


class LogConfig(BaseModel):
    """日志与遥测配置"""

    model_config = {"extra": "ignore"}

    # LLM 用量入库开关（可退出的遥测）：默认保持 True，Dashboard 的
    # 用量统计依赖该数据；关闭后 chat/摘要等调用不再写 usage_logs。
    usage_tracking: bool = True

    # 日志级别（DEBUG/INFO/WARNING/ERROR），默认 INFO
    level: str = "INFO"

    # 日志轮转：是否启用文件日志
    log_file_enabled: bool = False

    # 日志轮转：单文件最大字节数（默认 10 MB）
    log_file_max_bytes: int = 10 * 1024 * 1024

    # 日志轮转：保留备份数（默认 5）
    log_file_backup_count: int = 5

    # 日志目录（相对路径基于 app_root，默认 logs/）
    log_dir: str = "logs"

    # 数据保留天数：messages/usage_logs/audit_logs/sessions 中超过保留期的
    # 记录由 Bot 定期清理，防止长期运行单表无限膨胀；0 或负数 = 不清理（保留全部）
    retention_days: int = 0

    # 框架级消息记录/广播开关：为 True 时由 Dispatcher 对全部收发消息统一
    # 写库 + WS 实时广播（默认开，保证消息日志页"全量"）。关闭后仅内置 chat
    # 插件的 LLM 对话仍然记录（兼容旧行为）。
    record_all_messages: bool = True

    # 运行日志采集开关：为 True 时由 RunLogHandler 把运行日志采集进环形缓冲
    # 并经 /api/ws/runlog 实时推送到 WebUI"运行日志"页（默认开，方便排障）。
    # 关闭后运行日志页无数据。
    run_log_enabled: bool = True


class RAGConfig(BaseModel):
    """知识库（RAG）配置（默认关闭）

    支持两种检索模式：
    - keyword: 纯 Python 关键词检索，无外部依赖，适合小规模文档
    - vector:  LanceDB 向量检索 + litellm embedding，语义匹配更精准
    """

    model_config = {"extra": "ignore"}

    enabled: bool = False
    mode: Literal["keyword", "vector"] = "keyword"  # 检索模式
    embedding_model: str = ""  # 向量模型（vector 模式使用，如 text-embedding-3-small）
    embedding_api_url: str = ""  # 向量 API 地址（留空复用 llm.api_url）
    embedding_api_key: str = ""  # 向量 API Key（留空复用 llm.api_key）
    top_k: int = 3  # 检索时返回的最相关条目数
    knowledge_dir: str = "data/knowledge"  # 知识库目录（相对项目根目录）
    chunk_size: int = 400  # 文档分块大小（字符数）
    chunk_overlap: int = 50  # 相邻分块重叠字符数
    max_inject_chars: int = 800  # 注入 system_prompt 的参考资料长度上限（字符）
    collection_name: str = "qingci_knowledge"  # LanceDB 集合名（vector 模式使用）


class MarketConfig(BaseModel):
    """插件市场配置"""

    # 默认指 Gitee 镜像（GitHub 主仓库的国内同步镜像，拉取更快）；如遇镜像延迟，可改为 GitHub 主仓库 https://github.com/Qingci-Bot/Plugin-Market.git
    url: str = "https://gitee.com/qingci-bot/Plugin-Market.git"
    # 备用市场源（可选）：主源拉取失败时自动回退（如 GitHub 主仓库 / 自定义镜像）
    mirror_url: str | None = None
    refresh_interval: float = 3600  # 索引缓存 TTL（秒）


class TelegramConfig(BaseModel):
    """Telegram 平台适配器配置"""

    name: str = "telegram"
    enabled: bool = False
    token: str = ""  # Bot API token（@BotFather 获取）
    poll_interval: float = 1.0  # 长轮询间隔（秒）
    request_timeout: float = 40.0  # HTTP 请求超时（秒），须大于长轮询 timeout（30）
    max_retries: int = 0  # 网络传输错误最多重试次数（0 不重试，避免发送类重复）


class OneBot12Config(BaseModel):
    """OneBot 12 平台适配器配置（原生反向 WebSocket，事件无需翻译）"""

    name: str = "onebot12"
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 3002  # 与 v11 的 onebot.port 区分，避免端口冲突
    access_token: str = ""


class PlatformsConfig(BaseModel):
    """多平台适配器配置（onebot 为内置默认平台，走 onebot.* 配置）"""

    telegram: TelegramConfig = TelegramConfig()
    onebot12: OneBot12Config = OneBot12Config()


class ApiConfig(BaseModel):
    """API 层配置"""

    model_config = {"extra": "ignore"}

    # 是否信任反向代理的 X-Forwarded-For 头（用于登录防暴力限流的来源 IP 判定）。
    # 仅在明确部署于可信反向代理之后时才应开启；直连场景开启会被攻击者伪造头绕过限流。
    trust_proxy_headers: bool = False


class AppConfig(BaseModel):
    """应用总配置"""

    model_config = {"extra": "ignore"}

    api: ApiConfig = ApiConfig()
    bot: BotConfig = BotConfig()
    onebot: OneBotConfig = OneBotConfig()
    llm: LLMConfig = LLMConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    filter: FilterConfig = FilterConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    hot_reload: HotReloadConfig = HotReloadConfig()
    alert: AlertConfig = AlertConfig()
    image: ImageConfig = ImageConfig()
    render: RenderConfig = RenderConfig()
    rag: RAGConfig = RAGConfig()
    session_summary: SessionSummaryConfig = SessionSummaryConfig()
    log: LogConfig = LogConfig()
    market: MarketConfig = MarketConfig()
    platforms: PlatformsConfig = PlatformsConfig()
    api_key: str = ""  # API 鉴权密钥，为空则不启用鉴权
    plugins: dict = {}  # 插件级配置：plugins.<name>: { ... }
    lang: str = "zh-CN"  # 全局语言（插件 i18n 默认语言，如 zh-CN / en-US）


# ============ 配置管理器 ============

DEFAULT_CONFIG_PATH = app_root() / "config.yaml"


class ConfigManager:
    """配置管理器：加载、保存、热更新"""

    def __init__(self, path: Path | None = None):
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

    @property
    def rate_limit(self) -> RateLimitConfig:
        return self._config.rate_limit

    @property
    def filter(self) -> FilterConfig:
        return self._config.filter

    @property
    def scheduler(self) -> SchedulerConfig:
        return self._config.scheduler

    @property
    def hot_reload(self) -> HotReloadConfig:
        return self._config.hot_reload

    @property
    def alert(self) -> AlertConfig:
        return self._config.alert

    @property
    def image(self) -> ImageConfig:
        return self._config.image

    @property
    def render(self) -> RenderConfig:
        return self._config.render

    @property
    def rag(self) -> RAGConfig:
        return self._config.rag

    @property
    def session_summary(self) -> SessionSummaryConfig:
        return self._config.session_summary

    @property
    def log(self) -> LogConfig:
        return self._config.log

    @property
    def market(self) -> MarketConfig:
        return self._config.market

    @property
    def platforms(self) -> PlatformsConfig:
        return self._config.platforms

    def load(self) -> AppConfig:
        """从文件加载配置，不存在则创建默认配置"""
        with self._lock:
            if self._path.exists():
                try:
                    with open(self._path, encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    self._config = AppConfig(**data)
                except yaml.YAMLError as e:
                    logger = logging.getLogger("qingci-bot.config")
                    logger.error(f"配置文件 YAML 格式错误: {e}")
                    self._backup_corrupt_config()
                    self._config = AppConfig()
                    self.save()
                except ValidationError as e:
                    logger = logging.getLogger("qingci-bot.config")
                    logger.error(f"配置字段验证失败: {e}")
                    self._backup_corrupt_config()
                    self._config = AppConfig()
                    self.save()
                except Exception as e:
                    logger = logging.getLogger("qingci-bot.config")
                    logger.error(f"配置文件解析失败，使用默认配置: {e}")
                    self._backup_corrupt_config()
                    self._config = AppConfig()
                    self.save()
            else:
                self.save()
            return self._config

    def _backup_corrupt_config(self) -> None:
        """配置损坏时先备份原文件，避免用户配置被默认值不可逆覆盖"""
        try:
            import shutil

            bak = self._path.with_suffix(self._path.suffix + ".bak")
            shutil.copy2(self._path, bak)
            logging.getLogger("qingci-bot.config").warning(f"损坏的配置文件已备份到 {bak}")
        except OSError:
            pass  # 备份失败不阻塞启动，仅记录

    def save(self):
        """保存配置到文件（原子写入）"""
        with self._lock:
            import os
            import tempfile

            data = self._config.model_dump()
            # 先写入临时文件，再原子重命名
            tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
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
        """更新配置并保存（深合并语义，save 失败时回滚内存）

        仅覆盖传入的节/字段，未提供的配置保持不变，杜绝"传部分配置
        静默重置其它节（如 api_key / llm.api_key）"的陷阱。列表按整值
        替换（如 admin_users），嵌套 dict 递归合并。
        """
        with self._lock:
            merged = _deep_merge(self._config.model_dump(), data)
            new_config = AppConfig(**merged)
            old_config = self._config
            self._config = new_config
            try:
                self.save()
            except Exception:
                self._config = old_config
                raise

    def reload(self) -> AppConfig:
        """重新加载配置

        注意：reload 会新建 AppConfig 实例。经 ConfigManager 属性访问的
        组件自动取到新值；但缓存了子配置对象引用的消费方（如
        LLMManager）持有的旧引用不会生效，需由调用方（见
        api/routes/config.py 的 _maybe_notify_bot）显式传递新引用。
        """
        return self.load()

    def to_dict(self) -> dict:
        return self._config.model_dump()

    def get_plugin_config(self, plugin_name: str) -> dict | None:
        """获取插件级配置（config.yaml 中 plugins.<name> 节）"""
        with self._lock:
            raw = self._config.model_dump()
            plugins_section = raw.get("plugins", {})
            if not isinstance(plugins_section, dict):
                return None
            return plugins_section.get(plugin_name)

    def set_plugin_config(self, plugin_name: str, values: dict) -> dict:
        """设置插件级配置并保存到 config.yaml（先校验后原子写入，失败回滚内存）"""
        with self._lock:
            plugins = dict(self._config.plugins or {})
            plugins[plugin_name] = dict(values)
            # 先整体校验：pydantic 校验失败直接抛出，绝不落盘
            AppConfig(**{**self._config.model_dump(), "plugins": plugins})
            old_plugins = self._config.plugins
            self._config.plugins = plugins
            try:
                self.save()
            except Exception:
                # 写盘失败回滚内存，避免内存与磁盘不一致
                self._config.plugins = old_plugins
                raise
            return dict(plugins[plugin_name])
