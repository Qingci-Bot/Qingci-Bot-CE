# Changelog

All notable changes to Qingci-Bot CE will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- 实例管理：侧边栏新增实例列表，支持新建/删除/切换/重命名实例
- 实例=完全自包含目录（`instances/<name>/`，含 `config.yaml` + `plugins/` + `data/`），新增 `--instance <name>` 一次性决定 config/data_root/plugins/port 四个维度，可整体复制/迁移/备份
- 新增实例管理 API：`GET/POST/DELETE /api/instances`、`PUT /api/instances/{name}`（重命名目录 + 更新元数据）、`POST /api/instances/{name}/start`（重启进程到目标实例）
- 运行中实例也支持重命名：改名后自动重启到新名称（复用切换实例的 relaunch 机制），避免 Windows 文件锁阻止目录改名
- `bot.paths` 新增 `plugins_dir()`/`set_plugins_dir()`：外部插件代码目录默认 `app_root()/plugins`，实例模式下指向实例内 `plugins/`
- 命令权限等级显示：`Permission` 新增 `label` 属性，内置权限标注可读标签，组合（`&`/`|`/`~`）自动生成组合标签；`describe_permission()` 返回可读标签；命令管理接口追加 `permission` 字段，Web「命令管理」表格新增权限列并映射为中文（超级管理员/管理员/所有人等）
- `on_message` 新增 `description` 参数（与 `on_command`/`on_startswith`/`on_keyword` 对齐），存入 `meta.description` 供 `/help` 与命令管理展示

### Changed
- 移除全局模式：启动必须绑定一个实例（无实例时自动创建 `default`，未指定 `--instance` 时自动启动到默认实例）
- 外部插件目录、在线安装、热重载、插件元数据发现统一改用 `plugins_dir()`，不再硬编码 `app_root()/plugins`
- 端口自动分配从 8080 起：首个实例占用 8080，后续实例依次递增
- 构建脚本 `build.ps1` 不再生成根级 `config.yaml`/`data\`：配置/插件/数据均在实例自包含目录内，产物随实例目录分发
- 优化实例管理区块样式：改用全局 CSS 变量统一配色，增强「新建/重命名/删除/切换」按钮对比度与交互（hover 高亮、当前实例琥珀高亮）

### Fixed
- 修复"切换实例/重启"实际从未生效：旧实现用 `os._exit` 终止进程，会一并杀死等待重启的后台线程，导致新进程从未被拉起。改为派发独立分离的助手进程（`desktop/relaunch.py`）等待旧进程退出后再拉起目标实例
- 修复根级 `config.yaml` 残留/兜底生成：API 鉴权与配置接口在未显式指定 `--config` 时改用 `bot.instances.default_config_path()`（默认实例的 `config.yaml`），不再回退到 `app_root()/config.yaml`，保证配置始终落在实例自包含目录内

## [1.5.0] - 2026-08-16

### Added
- 管理员细分为**超级管理员**（唯一，`bot.super_admin`）与**普通管理员**（多个，`bot.admin_users`）：`SUPERUSER` 权限仅超级可命；`ADMIN` 权限普通管理员可命，超级管理员自动继承
- 多实例支持：新增 `--data-dir` 参数指定可写数据根目录（DB/日志/插件数据等），在同一台机器上可运行多个相互隔离的实例
- 单实例保护升级：互斥名由数据根目录派生，同一实例（同数据目录）重复双击聚焦已有窗口，不同实例（不同 `--data-dir`）互不阻塞可多开

### Changed
- 内置命令权限映射：`/status`、`/clear` 降级为普通管理员（`ADMIN`）；`/blacklist`、`/filter`、`/group`、`/kb` 保持超级管理员（`SUPERUSER`）
- 限流豁免、聊天敏感词 `exempt_admins` 豁免、错误告警通知目标均同时纳入超级管理员
- 数据库、日志、备份、插件数据等所有可写路径统一改为基于 `data_root()` 解析，尊重 `--data-dir` 设置

### Fixed
- 修复双击 exe / 重复启动会新建多个界面与进程：在入口加入单实例保护（Windows 命名互斥量），重复启动时聚焦已有窗口并退出，避免多窗口与端口冲突

## [1.4.1] - 2026-08-14

### Fixed
- 修复内置插件（`bot/plugin/builtin/*`）从单文件迁移到目录结构后相对导入层级错误（`from ..base` → `from ...base` 等），导致内置插件在源码与打包产物中均无法加载
- `load_builtin` 增加显式内置插件清单回退：PyInstaller 打包后 `pkgutil.iter_modules` 无法扫描 PYZ 归档内模块，扫描落空时按 `_BUILTIN_PLUGINS` 清单加载（与 `qingci-bot-ce.spec` 的 hiddenimports 保持一致）

### Docs
- 同步 `ARCHITECTURE.md`、`docs/PROJECT_STRUCTURE.md` 的目录树与当前实现：修正 PyInstaller spec 文件名（`qingci-bot-ce.spec`）、`bot/core/` 补 `event_bus.py`、`bot/plugin/` 补 `ratelimit.py`/`llm_tool.py`/`watcher.py`、`builtin/` 与 `plugins/` 由单文件改为目录结构；`PLUGIN_DEV.md` 修正 spec 文件名引用

## [1.4.0] - 2026-08-14

### Added
- 参数级依赖注入：Matcher handler 参数按签名自动解析注入（`MatcherContext`、`Bot`、DI 服务），支持 `Depends(...)` 显式声明与类型注解自动注入
- 全局生命周期钩子：插件可覆写 `on_startup` / `on_shutdown` / `on_bot_connect` / `on_metaevent`，在 Bot 启动/停止、LLBot 连接建立、元事件到达时获得通知（异常隔离）
- 插件数据目录：`PluginBase.data_dir` 属性，提供插件专属数据目录 `data/plugins/<name>/`（自动创建，卸载不删除）
- 在线插件安装：`PluginManager.install(bot, source)` 支持从 git 仓库、HTTP 归档 URL、本地目录/归档安装插件到 `plugins/` 并自动安装 `requirements.txt`（或 `plugin.json` 的 `requirements` 字段）声明的依赖
- 国际化（i18n）：`I18n` 翻译器 + 插件 `i18n/<locale>.json` 翻译资源自动加载，`self.i18n` / `self._` 使用；新增 `config.yaml` 的 `lang` 字段控制全局语言（默认 `zh-CN`）
- 事件总线：`EventBus` 跨插件发布-订阅事件广播，插件无需显式依赖即可协作；支持 `subscribe`/`publish`、通配订阅 `"*"`、sync/async handler、线程安全；注入到 `PluginBase.event_bus` 与 DI 容器
- 插件级 LLM 工具声明：`@llm_tool` 装饰器让插件注册 Function Calling 工具，参与 LLM 推理；工具名自动加插件名前缀（`<plugin>_<name>`），卸载时自动注销
- 指令系统增强：`on_command` 新增 `aliases`（命令别名）、`subcommands`（子指令路由）、`args_schema`（类型化参数解析并按名注入 handler 形参）
- 配置 schema 自动生成：插件定义 `Config` 内嵌类（pydantic）自动导出 JSON Schema，Web 插件管理页据此渲染配置表单，无需手写 UI；新增 `GET/PUT /api/plugin/{name}/config` 接口
- 自动热重载：`PluginWatcher` 监听外部插件目录文件变更并自动重载插件（开发期提效）；由 `config.yaml` 的 `hot_reload.enabled` / `hot_reload.interval` 控制，默认关闭
- 细粒度事件处理钩子：新增 Matcher 运行前全局钩子（`run_preprocessor`，`bot.add_matcher_preprocessor`，在 Matcher 匹配成功后、handler 前触发，返回非 None 即拦截该 Matcher）与平台接口调用钩子（`on_calling_api`，`bot.register_api_hook` / `connection.on_api_call`，每次 OneBot API 调用前触发，可改写参数或抛异常阻止调用）

### Fixed
- 修复 `DELETE /api/log/sessions/one` 接口（`delete_session`）使用 `Request | None` 参数导致 FastAPI 启动时路由注册失败、API 无法启动的问题

### Changed
- mypy 配置 `python_version` 由 3.10 提升至 3.12，与推荐运行版本保持一致
- 代码质量维护：修复 ruff 检查 318 处、mypy 类型检查 82 处错误，并对 58 个文件统一格式化；`bot/db/database.py` 针对 SQLModel 列访问按文件禁用相关 mypy 错误码

## [1.3.0] - 2026-08-13

### Added
- 插件 Web 管理页面：`register_page(title, icon, static_dir)` 方法，插件可在 `on_load` 中注册管理页面入口；框架自动挂载插件静态文件到 `/api/plugin-data/{name}/`；前端插件管理页展示「管理」按钮，点击后右侧抽屉 iframe 加载
- 插件目录结构：`load_external_dir()` 支持目录型插件（`plugins/<name>/__init__.py`），可含 `web/` 子目录和 `plugin.json`；同名时目录型优先于文件型
- 命令管理：`Matcher.disabled` 字段支持禁用单个命令；`GET /api/command/conflicts` 列出所有命令并标记冲突；`PUT /api/command/{owner}/{command}` 支持禁用/启用/调整优先级；前端插件管理页新增「命令管理」Tab，冲突行红色高亮

### Changed
- 内置插件全部转为目录结构：`admin/`、`chat/`、`help/`、`imagegen/`、`knowledge/`
- 外部插件示例和模板转为目录结构：`hello/`、`_template/`
- 插件 API 列表接口追加 `pages` 字段

### Fixed
- 修复 `check_availability` 在 litellm 导入失败时引用未绑定局部变量导致 `NameError` 二次崩溃
- 修复中间件拦截 Matcher 时指标重复计数

## [1.2.1] - 2026-08-11

### Fixed
- 修复 `request` Matcher 审批结果（True/False）被丢弃，导致加好友/加群审批永不执行
- 修复 `chat()` 被取消时用户消息不回滚，导致内存与数据库残留孤立消息
- 修复连接监控回调 `awaitable` 判断使用 `asyncio.iscoroutine()` 无法识别 Future/Task，改用 `inspect.isawaitable()`

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

[1.4.0]: https://atomgit.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.4.0
[1.3.0]: https://atomgit.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.3.0
[1.2.1]: https://atomgit.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.2.1
[1.2.0]: https://atomgit.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.2.0
[1.1.0]: https://atomgit.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.1.0
[1.0.0]: https://atomgit.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.0.0