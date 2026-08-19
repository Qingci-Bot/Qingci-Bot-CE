"""组合根（Composition Root）— 集中 QingciBot 的组件装配

将 QingciBot.__init__ 中散落的组件创建与 DI 注册收敛到单一函数：
- __init__ 只保留配置加载与状态字段声明，装配逻辑统一在此
- 便于测试替换实现（注入 fake connection / 内存 db 等），
  也避免 __init__ 随功能增长持续膨胀

注意：本模块与 bot.py 存在类型级循环依赖（composition 装配 QingciBot），
因此对 QingciBot 仅做 TYPE_CHECKING 导入，运行时无循环导入。
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import ConfigManager
from ..db import Database
from ..filter import SensitiveFilter
from ..html_renderer import HtmlRenderer
from ..llm import (
    EventBuffer,
    LLMManager,
    ToolRegistry,
    register_builtin_tools,
    register_event_tools,
)
from ..paths import data_root
from ..plugin import PluginManager
from ..plugin.protocol.ratelimit import RateLimiter
from ..rag import KnowledgeStore
from .di import DIContainer
from .dispatcher import MessageDispatcher
from .event_bus import EventBus
from .platforms.base import make_platform
from .platforms.onebot11 import OneBotConnection
from .scheduler import BotScheduler
from .session_state import SessionStateManager

logger = logging.getLogger("qingci-bot.core.composition")

if TYPE_CHECKING:
    from .bot import QingciBot


def assemble_bot(bot: "QingciBot") -> None:
    """装配 QingciBot 的全部组件并注册到 DI 容器（仅 __init__ 调用一次）"""
    config: ConfigManager = bot.config

    # ---- 核心服务 ----
    bot.db = Database()
    bot.connection = OneBotConnection(
        host=config.onebot.host,
        port=config.onebot.port,
        access_token=config.onebot.access_token,
        enabled=config.onebot.enabled,
    )
    # 平台适配器表：onebot 为默认平台，其余按 platforms 配置启用
    # （connection 保持主连接引用以兼容现有代码，同时注册到 platforms）
    bot.platforms = {"onebot": bot.connection}  # type: ignore[attr-defined]
    _platforms_cfg = config.platforms
    for _attr in ("telegram", "onebot12"):
        _cfg = getattr(_platforms_cfg, _attr, None)
        if _cfg is None or not getattr(_cfg, "enabled", False):
            continue
        _adapter = make_platform(_cfg)
        if _adapter is not None:
            bot.platforms[_adapter.name] = _adapter
            logger.info(f"平台适配器已注册: {_adapter.display_name} ({_adapter.name})")
    bot.dispatcher = MessageDispatcher()
    bot.llm = LLMManager(
        config.llm,
        db=bot.db,
        summary_config=config.session_summary,
        usage_tracking=config.log.usage_tracking,
    )
    # HTML → 图片渲染服务（可选能力；playwright 未安装时渲染不可用，
    # 不影响启动。能力探测在 bot.start 中后台执行）
    bot.html_renderer = HtmlRenderer(config.render)
    bot.plugin_manager = PluginManager()

    # 依赖注入容器：集中管理所有服务实例
    bot.di = DIContainer()
    # 会话状态：TTL 键值存储，用于多步骤对话、表单、临时缓存
    bot.session_state = SessionStateManager()
    # 事件总线：跨插件发布-订阅事件广播，解耦插件间协作
    bot.event_bus = EventBus()

    # ---- 注册所有服务到 DI 容器（sync 兼容 __init__ 同步上下文）----
    di: DIContainer = bot.di
    di.register_sync(ConfigManager, config)
    di.register_sync(Database, bot.db)
    di.register_sync(OneBotConnection, bot.connection)
    di.register_sync(LLMManager, bot.llm)
    di.register_sync(HtmlRenderer, bot.html_renderer)
    di.register_sync(PluginManager, bot.plugin_manager)
    di.register_sync(MessageDispatcher, bot.dispatcher)
    di.register_sync(SessionStateManager, bot.session_state)
    di.register_sync(EventBus, bot.event_bus)
    di.register_sync(DIContainer, di)
    # 注册自身（使插件可以注入 bot 引用；延迟导入避免模块级循环）
    from .bot import QingciBot

    di.register_sync(QingciBot, bot)

    # ---- 功能增强组件 ----
    # 限流器实例（rate_limit.enabled 时创建；创建于核心层，
    # 不再依赖某个内置插件，避免插件缺失时限流静默失效）
    rl_cfg = getattr(config, "rate_limit", None)
    bot.rate_limiter = None
    if rl_cfg is not None and rl_cfg.enabled:
        bot.rate_limiter = RateLimiter(
            daily_limit=rl_cfg.daily_limit,
            cooldown_seconds=rl_cfg.cooldown_seconds,
        )
    # 定时任务调度器（start/stop 中启动与关闭）
    bot.scheduler = BotScheduler()
    # 插件开发期自动热重载监听器（hot_reload.enabled 时 start 中启动，stop 中关闭）
    bot._plugin_watcher = None
    # Function Calling 工具注册表（常驻创建，轻量；
    # 仅在 llm.enable_tools 开启且模型支持 tools 时才实际参与调用）
    bot.tool_registry = ToolRegistry()
    register_builtin_tools(bot.tool_registry)
    # 类型化事件缓冲（notice/request 事件环形记录，供 LLM 事件查询工具读取）
    bot.event_buffer = EventBuffer()
    register_event_tools(bot.tool_registry, bot.event_buffer)
    bot.llm.set_tool_registry(bot.tool_registry)
    # 知识库（rag.enabled 时创建，未启用时为 None）
    bot.knowledge_store = None
    if config.rag.enabled:
        rag_cfg = config.rag
        knowledge_dir = Path(rag_cfg.knowledge_dir)
        if not knowledge_dir.is_absolute():
            knowledge_dir = data_root() / knowledge_dir
        llm_cfg = config.llm
        bot.knowledge_store = KnowledgeStore(
            root=knowledge_dir,
            mode=rag_cfg.mode,
            chunk_size=rag_cfg.chunk_size,
            chunk_overlap=rag_cfg.chunk_overlap,
            top_k=rag_cfg.top_k,
            embedding_model=rag_cfg.embedding_model,
            embedding_api_url=rag_cfg.embedding_api_url or llm_cfg.api_url,
            embedding_api_key=rag_cfg.embedding_api_key or llm_cfg.api_key,
            collection_name=rag_cfg.collection_name,
        )
    # 敏感词过滤器：词库路径相对项目根目录解析
    # （enabled 开关由拦截逻辑判断，此处仅构造）
    words_file = Path(config.filter.words_file)
    if not words_file.is_absolute():
        words_file = data_root() / words_file
    bot.sensitive_filter = SensitiveFilter(words_file)


def build_bot(config_path: str | None = None) -> "QingciBot":
    """便捷入口：构造并装配一个 QingciBot（等价 QingciBot(config_path)）

    供测试与外部脚本使用，避免直接依赖 QingciBot 构造器的装配细节。
    """
    from .bot import QingciBot

    return QingciBot(config_path)
