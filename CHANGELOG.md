# Changelog

All notable changes to Qingci-Bot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-08-10

### Added
- 插件临时禁用/启用：`enabled` 字段 + `on_disable`/`on_enable` 钩子 + API 端点 + 前端开关
- 插件状态管理：`PluginStatus` 枚举（LOADING/LOADED/DISABLED/ERROR/UNLOADING）替代原布尔值
- 插件级配置：`config.yaml` 中 `plugins.<name>` 节，插件可定义 `Config` 内嵌类自动校验
- 插件导出/导入机制：`export()` / `require()` 方法，支持插件间服务接口暴露
- 插件级中间件：`register_before()` / `register_after()` 钩子，可拦截/修改 handler 返回值
- 插件分类：`category` 字段 + 前端分类标签页筛选
- 执行指标监控：Matcher 调用次数、平均耗时、错误率 + API `/api/plugin/{name}/metrics` + 前端指标面板
- 插件元数据发现：`plugin.json` 文件 + API `/api/plugin/discover/metadata`，无需导入模块即可发现插件信息
- 帮助命令输出增强：按插件分类分组展示，支持权限过滤
- 依赖版本约束：`require` 支持 PEP 440 版本规范（如 `"chat>=1.0,<2.0"`）
- Plugins-Dev SDK 同步：新增 `PluginStatus`、`category`、`export`/`require`、中间件、`plugin_config` 等字段
- 会话状态管理：`SessionState` + `SessionStateManager`，TTL 键值存储，`ctx.session_state` 便捷访问
- 会话状态优化：`asyncio.Lock` 并发安全、`pop`/`expire`/`ttl`/`items` 便捷方法、`remove_session` 显式删除、`stats` 统计、`serialize`/`deserialize` 持久化、会话数上限保护
- 依赖注入容器：`DIContainer` 按类型自动注入，支持 SINGLETON/TRANSIENT/SCOPED 生命周期
- DI 容器优化：`asyncio.Lock` 并发安全、`register_as` 接口绑定、`Optional[X]` 类型提取、`inject` 不覆盖已赋值属性、`register_sync`/`inject_sync` 同步兼容方法
- 插件测试工具：`bot.testing` 包（TestBot 轻量测试环境 + 事件构造器），插件作者可用 pytest 模拟消息事件、断言回复与主动发送

### Fixed
- 修复 `PluginBase.require` 属性与同名方法冲突：方法改为 `get_exports()`
- 修复 `SessionStateManager.get_session` 清理时误删刚创建的会话导致 `KeyError`
- 修复 `Matcher` 不可哈希导致指标记录 `TypeError`：改为身份比较 dataclass
- 修复 `MatcherContext` 缺 `Any` 导入
- 修复首次启动向导（Setup Wizard）无鉴权可篡改配置：已配置 `api_key`/`admin_users` 后禁止重复引导
- 修复插件元数据发现接口导入路径错误导致 500
- 敏感字段脱敏增强：改为后缀匹配，覆盖 `embedding_api_key` 等字段
- 修复启动画面（splash）关闭后窗口无法销毁
- 修复 RAG 向量库同步方法阻塞事件循环：新增 `*_async` 异步方法并更新调用方
- 修复配置损坏时静默覆盖原文件：损坏文件先备份为 `.bak` 再重建默认配置
- 修复一次性（temp）Matcher 在 handler 异常后残留：移除操作移入 `finally`
- `Message.append` 支持展开列表/元组参数，非法类型抛 `TypeError`
- 修复 `LLMManager.clear_session` 内存与 DB 清除竞态：DB 清除移入会话锁内
- 修复 MCP 工具注册失败时桥接器资源泄漏：未注册 bridge 在 `finally` 中关闭
- 修复 `LLMManager.reload` 锁快照竞态：新增全局重载锁，防新建会话逃过清空
- 修复 API 删除会话绕过 LLMManager 导致内存历史"复活"：新增 `clear_session_by_key` 统一清理
- 修复会话历史查询无 id 兜底排序导致的乱序
- 修复 `GROUP_MEMBER` 权限语义：仅群聊消息生效，私聊一律不匹配
- 修复 `is_connected` 仅检测 API 通道：事件通道连接也视为已连接
- 修复对话调试 WebSocket 收到非法 `user_id` 导致连接异常断开

## [1.1.0] - 2026-08-10

### Added
- 依赖分组：`[test]` / `[build]` / `[dev]` 三级拆分，按需安装
- 代码质量工具：ruff（代码风格）、mypy（类型检查）、pre-commit hooks
- 测试用例：API 端点、配置管理、数据库操作（pytest + pytest-cov）
- 文档：CHANGELOG.md、CONTRIBUTING.md、SECURITY.md
- 前端工程化：ESLint + Prettier 配置
- 全局 API 异常处理器（统一错误响应格式）
- 日志轮转：按文件大小轮转，保留最近 N 个备份（`log.log_file_enabled` 等配置）
- 模块级 Logger（`logging.getLogger("qingci-bot.xxx")`）

### Changed
- README 大幅更新：依赖表格、测试/代码质量章节、文档链接集中管理
- 开发依赖安装命令从 `pip` 改为 `uv pip`，虚拟环境创建统一用 `uv venv`

### Fixed
- 启动窗口期事件丢失：`_running` 标志提前到 `connection.start()` 后立即设置
- `stop()` 部分清理遗漏：新增 `_started` 标志，部分启动失败时仍正常清理资源
- `LLMManager.reload()` 锁获取中断风险：用 `acquired` 列表追踪已获取锁
- `chat_with_tools()` 空指针防御：`registry` 为 None 时跳过工具执行

## [1.0.0] - 2026-08-10

### Added
- 完整的 QQ Bot 框架，对接 LLBot（OneBot 11 协议）
- 多 LLM 提供商支持：OpenAI / DeepSeek / Ollama / SiliconFlow / Claude / Gemini / 自定义
- 插件系统：借鉴 NoneBot2 的 Matcher / Rule / Permission 设计，支持热加载
- Web 管理端：Vue 3 + 原神风格暗色主题
- 桌面应用：PyWebView 套壳 + 系统托盘 + 开机自启 + 启动加载画面
- 轻量知识库：关键词 + LanceDB 向量检索双模式
- 会话摘要：历史超长时自动压缩，保留最近 N 轮原文
- Function Calling：支持多轮工具调用（含 MCP 服务器接入）
- 定时任务调度器（基于 APScheduler）
- 错误告警：ERROR 日志达到阈值时私聊通知管理员
- 敏感词过滤
- 群粒度配置（启用/禁用 + 触发模式）
- 人格切换（/persona 命令）
- LLM 用量统计
- 审计日志
- 数据备份与恢复
- 流式对话（WebSocket + SSE）
- 完全离线运行（无 CDN 依赖）
- 一键打包为 Windows EXE（PyInstaller）

[1.2.0]: https://atomgit.com/luoqingci/Qingci-Bot/releases/tag/v1.2.0
[1.1.0]: https://atomgit.com/luoqingci/Qingci-Bot/releases/tag/v1.1.0
[1.0.0]: https://atomgit.com/luoqingci/Qingci-Bot/releases/tag/v1.0.0