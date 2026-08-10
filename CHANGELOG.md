# Changelog

All notable changes to Qingci-Bot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.1.0]: https://atomgit.com/luoqingci/Qingci-Bot/releases/tag/v1.1.0
[1.0.0]: https://atomgit.com/luoqingci/Qingci-Bot/releases/tag/v1.0.0