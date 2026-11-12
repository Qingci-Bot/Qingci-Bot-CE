# 项目结构规范

> 本文档定义 Qingci-Bot-CE 的目录职责、文件组织与产物归属约定，是结构相关变更的准绳。

## 1. 仓库定位

`Qingci-Bot-CE` 是 `Qingci-Bot` 根目录下的**多个独立子项目之一**。根目录还包含其他子项目（如 `Plugins-SDK`、`Plugin-Market`），因此：

- 每个子项目自己管理自己的 `.git` 仓库与依赖；
- **任何子项目的运行产物（缓存、构建产物等）不得写入根目录**，必须留在自身目录内。
- 插件协议层（`PluginBase`/`Matcher`/`Permission`/`Rule`/`MessageContext`）的**唯一来源是 `Plugins-SDK`**；本仓库的 `bot/plugin/protocol/{base,matcher,permission,rule,ratelimit,session,events,context}.py` 均为薄转发（`from qingci_plugin_sdk.* import *`），`bot/plugin/` 顶层同名文件为兼容再导出，修改协议请改 SDK 而非此处。

### 产物归属约定

| 产物类型 | 允许位置 | 禁止位置 | 忽略规则 |
|----------|----------|----------|----------|
| Python 缓存 | `Qingci-Bot-CE/.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`__pycache__/` | 根目录或其他子项目 | `.gitignore` 已忽略 |
| 覆盖率 | `Qingci-Bot-CE/.coverage`、`htmlcov/` | 根目录 | 已忽略 |
| 前端依赖/构建 | `web/node_modules/`、`web/dist/`、`desktop/electron/node_modules`、`dist/`（electron-builder 输出） | 根目录 | 已忽略 |
| 虚拟环境 | `Qingci-Bot-CE/.venv/` | 根目录 | 已忽略 |
| 安装元数据 | `*.egg-info/` | 根目录 | 已忽略 |
| 运行时数据 | `data/`、`*.db` | 根目录 | 已忽略 |
| 实例配置/数据 | `instances/<name>/config.yaml`、`instances/<name>/data/` | 根目录 `config.yaml` | `.gitignore` 已忽略 `instances/` |

> 规则：**在 `Qingci-Bot-CE/` 目录下运行所有命令**（ruff、pytest、mypy、构建），使缓存落在本目录内，避免污染根目录。

## 2. 目录结构总览

```
Qingci-Bot-CE/
├── main.py                 # 后端入口（启动 Bot + API）
├── api/                    # FastAPI 接口层
│   ├── server.py           # 应用装配、路由挂载、中间件
│   ├── auth.py             # 鉴权 / 审计横切逻辑
│   ├── audit.py            # 登录/操作审计
│   └── routes/             # REST 路由：login/backup/bot/command/config/group/instances/log/market/metrics/plugin
├── bot/                    # Bot 核心逻辑（纯 Python 包）
│   ├── core/               # 框架层（生命周期与调度）：bot/composition/dispatcher/event_bus/
│   │                       #   di/scheduler/session_state/tasks/message(仅 v11 兼容)/
│   │                       #   v11_compat（v11→v12 事件翻译）/platforms（含 onebot11/onebot12/telegram）
│   │   ├── composition.py  # 组合根：assemble_bot() 组件装配 + DI 注册（__init__ 不再手写装配）
│   │   ├── bot.py          # Bot 主类；get_bot() 经 DI 容器解析（resolve_sync），无模块级单例
│   │   ├── dispatcher.py   # 消息分发 + Matcher 调度（MessageContext 引 protocol.context）
│   │   └── platforms/      # 多平台适配器：base.py（PlatformAdapter 契约 + 高频动作便捷方法 + _api_action 动作名映射）+ onebot11/onebot12/telegram
│   │                       #   OneBotConnection 实现契约作为「onebot」平台；回复按来源平台路由
│   ├── errors.py           # 统一错误模型（AppError 基类 + 领域错误类唯一定义处；新增领域错误继承 AppError）
│   ├── alerter.py          # 错误告警器（ERROR 日志阈值 → 私聊通知管理员）
│   ├── filter.py           # 敏感词过滤器（词库 + 打码）
│   ├── broadcast.py        # 消息广播（WS 实时推送 broker）
│   ├── metrics.py          # 运行指标导出（Prometheus 文本格式，零依赖、不查库）
│   ├── logformat.py        # 结构化 JSON 日志 + 文件轮转 + 运行日志采集（RunLogHandler，经 /api/ws/runlog 推送）
│   ├── logredact.py        # 日志脱敏（API Key / token 打码）
│   ├── html_renderer.py    # HTML → 图片渲染服务（Playwright 无头 Chromium，可选依赖）
│   ├── plugin/             # 插件系统
│   │   ├── protocol/       # 插件协议层（薄转发 SDK，唯一实现来源为 Plugins-SDK）
│   │   │   ├── base.py     # 薄转发 SDK PluginBase（协议层唯一来源）
│   │   │   ├── context.py  # 薄转发 SDK MessageContext
│   │   │   ├── matcher.py  # 薄转发 SDK Matcher 与匹配器工厂
│   │   │   ├── rule.py     # 薄转发 SDK Rule 规则系统
│   │   │   ├── permission.py # 薄转发 SDK Permission 权限
│   │   │   ├── ratelimit.py  # 薄转发 SDK RateLimiter 限流
│   │   │   ├── session.py  # 薄转发 SDK Session（会话阶梯，多轮交互）
│   │   │   └── events.py   # 薄转发 SDK 类型化事件（notice/request 事件模型）
│   │   ├── base.py         # 兼容再导出（指向 protocol/base.py，供存量导入路径）
│   │   ├── matcher.py      # 兼容再导出（指向 protocol/matcher.py）
│   │   ├── rule.py         # 兼容再导出（指向 protocol/rule.py）
│   │   ├── permission.py   # 兼容再导出（指向 protocol/permission.py）
│   │   ├── ratelimit.py    # 兼容再导出（指向 protocol/ratelimit.py）
│   │   ├── session.py      # 兼容再导出（指向 protocol/session.py）
│   │   ├── events.py       # 兼容再导出（指向 protocol/events.py）
│   │   ├── abi.py          # SDK ABI 能力探测（REQUIRED_SYMBOLS 符号清单 + 版本区间双检；解除 SDK 精确钉版）
│   │   ├── manager.py      # PluginManager 门面：加载/热重载/卸载/依赖/元数据 + SDK data_root 实例重定向
│   │   ├── manager_web.py  # Web 职责分组 mixin：管理页面挂载 / 插件级 API 收集 / 配置 Schema
│   │   ├── manager_metrics.py  # 指标职责分组 mixin：Matcher 执行指标记录与查询
│   │   ├── _rwlock.py      # 热重载读写屏障（分发共享读 / 重载独占写 / 同任务重入安全）
│   │   ├── _loading.py     # 加载辅助纯函数：内置插件清单 / 子指令展开 / 依赖版本解析 / plugin.json 读取
│   │   ├── installer.py    # 插件安装/卸载（来源拉取 + 归档安全解压 + 目录原子落位；manager 的薄委托实现）
│   │   ├── market.py       # 插件市场：索引拉取/缓存 + 安装/更新编排
│   │   ├── deps.py         # 插件第三方依赖自动安装（data_root()/deps/<name>/ 按插件隔离 + sys.path 注入）
│   │   ├── _proc.py        # 子进程公共标志（Windows 隐藏控制台窗口 CREATE_NO_WINDOW）
│   │   ├── ssrf.py         # SSRF 防护（插件网络请求目标校验）
│   │   ├── webapi.py       # 插件级 Web API 适配器（register_api → /api/plugin-web/<name>/）
│   │   ├── llm_tool.py     # @llm_tool 插件级 LLM 工具声明（注册到 ToolRegistry 的运行时逻辑，保留在本仓库）
│   │   ├── watcher.py      # 插件自动热重载监听
│   │   └── builtin/        # 内置插件：admin/chat/help/imagegen/knowledge
│   ├── llm/                # LLM 管理、适配器、工具调用（Function Calling / MCP）
│   │   ├── manager.py      # LLMManager
│   │   ├── adapter.py      # 适配器基类
│   │   ├── litellm_adapter.py
│   │   ├── mcp.py          # MCP 工具接入
│   │   ├── tools.py        # 工具注册（内置只读工具）
│   │   └── events_tools.py # 类型化事件缓冲 + 事件查询 LLM 工具
│   ├── db/                 # SQLModel ORM、仓储、会话管理
│   │   ├── database.py     # Database 会话/仓储（record_message 走写入缓冲）
│   │   ├── write_buffer.py # 消息写入缓冲（write-behind 批量提交：50 条 / 200ms）
│   │   ├── engine.py       # 数据库引擎
│   │   └── models.py       # ORM 模型
│   ├── rag/                # 知识库检索（关键词 + 向量；向量需可选依赖 lancedb）
│   │   └── knowledge.py    # KnowledgeStore（lancedb 缺失时 vector 模式自动回退 keyword）
│   ├── testing/            # TestBot 测试沙箱
│   │   ├── bot.py          # TestBot
│   │   └── events.py       # 事件工厂
│   ├── config.py           # ConfigManager（config.yaml 加载与校验）
│   ├── config_schema.py    # 配置 JSON Schema 生成器（WebUI 表单单一来源；敏感字段 x-secret 标记）
│   ├── instances.py        # 实例管理（instances/<name>/ 自包含目录，含 config/plugins/data）
│   ├── i18n.py             # 国际化翻译器
│   ├── paths.py            # 路径解析（app_root / data_root / plugins_dir 等）
│   └── __init__.py
├── web/                    # Vue 3 前端
│   ├── index.html          # 入口 HTML
│   ├── vite.config.js      # Vite 配置
│   ├── package.json        # npm 依赖与脚本
│   └── src/
│       ├── views/          # 页面级组件
│       ├── components/      # 通用组件（如 Drawer 抽屉）
│       ├── stores/          # Pinia 状态
│       ├── router/         # 路由
│       ├── composables/    # 组合式函数
│       └── styles/         # 全局样式
├── desktop/                # Electron-Python 混合架构的桌面层
│   ├── electron/           # Electron 桌面壳（main.js/preload.js/build），负责 spawn 后端、UI 加载、托盘/单实例/启动画面
│   ├── py/                 # Python 侧桌面辅助（single_instance.py 单实例互斥、relaunch.py CLI/headless 重启助手）
│   └── assets/             # 应用图标（app-icon.ico，Python exe + Electron 共用）
├── plugins/                # 外部插件目录（运行时加载；实例模式下为 instances/<name>/plugins）
│   ├── _template/          # 插件模板（下划线前缀 = 非正式/模板，不参与加载）
│   │   └── plugin.json     # 插件元数据模板
│   └── hello/              # 示例插件
├── instances/              # 实例注册表（运行时生成，启动必需；每个实例一个自包含目录，无全局模式）
├── migrations/             # Alembic 数据库迁移
│   └── versions/           # 版本迁移脚本
├── tests/                  # pytest 测试
│   ├── test_*.py           # 按被测模块命名
│   └── plugin_pkg/         # 测试用插件包（dep/di/p1/p2/sdk_llm_tool/subcmd_runtime 等）
├── scripts/                # 一次性/运维脚本（版本升级 / 迁移校验 / 后端打包 / SQLite→PostgreSQL 迁移）
├── docs/                   # 使用、配置、部署、前端、API、打包、排查与规范文档
├── build.bat               # Windows 构建脚本（打包前 -e 安装 Plugins-SDK）
├── build-electron.bat      # Electron 打包脚本（先 build.bat 出后端 onedir，再 electron-builder 出安装版 Setup.exe + 绿色解压 zip）
├── qingci-bot-ce.spec      # PyInstaller 打包配置（collect_all 打包 SDK）
├── pyproject.toml          # 依赖（qingci-plugin-sdk 为 PyPI 固定版本依赖）、ruff/mypy/pytest 配置
├── alembic.ini             # 迁移配置
├── .pre-commit-config.yaml # pre-commit 钩子
└── README.md / ARCHITECTURE.md / PLUGIN_DEV.md / CONTRIBUTING.md / CHANGELOG.md / SECURITY.md / CODE_OF_CONDUCT.md / LICENSE / NOTICE
```

## 3. 各目录职责与归属原则

| 目录 | 职责边界 | 禁止放入 |
|------|----------|----------|
| `api/` | 对外 HTTP 接口、鉴权、审计 | 业务逻辑（应下沉到 `bot/`） |
| `bot/core/` | **框架层**：生命周期、连接、调度、DI、组合根装配、事件总线、会话状态 | 具体业务与功能组件 |
| `bot/`（根级） | 功能/横切组件：alerter / filter / broadcast / logformat / html_renderer | 框架调度核心 |
| `bot/plugin/` | 插件系统机制（`protocol/` 协议层薄转发 SDK + 顶层兼容再导出 + 运行时 manager/market 等）+ `builtin/` 内置插件 | 框架强耦合的临时代码 |
| `bot/llm`、`bot/db`、`bot/rag` | 领域能力模块 | 与框架调度耦合的逻辑 |
| `web/` | 前端 UI | 后端逻辑 |
| `tests/` | 所有测试，按 `test_<模块>.py` 命名 | 生产代码 |
| `scripts/` | 一次性工具脚本 | 可被 `main.py`/`__init__` 覆盖的频繁调用逻辑 |

### 文档职责分工（避免内容漂移）

| 文件 | 唯一职责 | 不应放入 |
|------|----------|----------|
| `README.md` | 项目门面：简介、特性、快速开始、文档导航、合规声明、开发计划、许可概要 | 安装步骤细节、配置字段全表、命令参考 |
| `docs/SETUP.md` | 环境要求、安装、启动与部署、命令行参数、管理命令、API 鉴权与监控 | 配置字段语义、架构设计、插件 API |
| `docs/CONFIGURATION.md` | 协议端 / LLM 配置、完整 `config.yaml` 字段、环境变量、进阶开关 | 安装步骤、部署方式 |
| `ARCHITECTURE.md` | 架构分层、进程模型、技术栈、核心模块 | 逐步操作（进 SETUP / DEPLOYMENT） |
| `docs/DEPLOYMENT.md` | 向量检索初始化、PostgreSQL 迁移、协议端断连恢复 | 日常安装步骤 |
| `docs/FRONTEND.md` | 前端命令、页面路由、`web/src` 结构 | 后端/插件 API |
| `docs/API.md` | HTTP 端点与 WebSocket 清单、鉴权与错误约定 | 配置字段语义 |
| `docs/PACKAGING.md` | PyInstaller / Electron 打包、发布流水线 | 开发环境搭建 |
| `docs/TROUBLESHOOTING.md` | 按现象定位的故障排查 | 功能介绍 |
| `CODE_OF_CONDUCT.md` | 社区行为准则、报告与处理 | 贡献流程（进 CONTRIBUTING） |
| `PLUGIN_DEV.md` | 插件 API（Matcher / Rule / 钩子 / DI / 工具 / 测试） | 前端、打包、端点清单 |
| `CONTRIBUTING.md` | 贡献流程、开发环境、质量门禁、本地构建与发版 | 面向使用者的安装说明 |
| `docs/CODING_STANDARDS.md` | 类型 / 命名 / 分层 / Git 约定 | 目录职责（归本文档） |
| `docs/PROJECT_STRUCTURE.md` | 目录职责、产物归属、分层依赖方向 | 代码风格细则 |
| `SECURITY.md` | 支持版本、漏洞报告流程、安全实践 | 功能说明 |
| `CHANGELOG.md` | 版本变更记录 | 使用说明 |
| `LICENSE` / `NOTICE` | GPLv3 官方全文 / 附加条款与版权授权 | 任何项目说明（LICENSE 仅允许填 3 行占位符） |

> **已补齐**：生产部署迁至 `docs/DEPLOYMENT.md`；前端 / API / 打包分别迁至
> `docs/FRONTEND.md` / `docs/API.md` / `docs/PACKAGING.md`；新增 `docs/TROUBLESHOOTING.md`
> 与 `CODE_OF_CONDUCT.md`。`PLUGIN_DEV.md` 现只保留插件开发主题。
> 新增或移动文档后必须同步 `tests/test_docs.py` 的 `DOCS` 清单，否则代码块与链接检查会静默漏掉。

**分层依赖方向（禁止反向）：**

```
web/  ──HTTP──▶  api/  ──调用──▶  bot/  ──依赖──▶  bot/core, bot/llm, bot/db, bot/rag
                                     ▲
                              bot/plugin 依赖框架层，框架层不反向依赖插件
tests/ 可依赖任意模块，用于验证
```

**协议层依赖方向（唯一来源）：**

```
Plugins-SDK（qingci_plugin_sdk）  ──(正式依赖)──▶  bot/plugin/protocol/*.py（薄转发）
                                                        │（顶层 bot/plugin/*.py 为兼容再导出）
                        bot/core/dispatcher.py（MessageContext 引 protocol.context） ◄──┘
```

**分层约定（协同边界）：**

- **内核（`bot/core/*`）可直接引用 SDK**（`qingci_plugin_sdk` 为正式依赖）；**插件必须经 `bot/plugin/protocol/` 薄转发**，禁止插件直引 SDK——若未来 protocol/ 对某符号做兼容适配，内核直引与插件层走两条路径，code review 需把关。
- **声明机制归属 SDK、注册逻辑归属 CE**：`llm_tool` / `LlmToolSpec` / `begin_tool_collection` 等声明与收集机制的唯一实现在 SDK（`qingci_plugin_sdk.llm_tool`）；CE `bot/plugin/llm_tool.py` 仅转发并保留 CE 特有的 `register_tools`（注册进 ToolRegistry）。插件从 `qingci_plugin_sdk` 或 `bot.plugin.llm_tool` 导入装饰器走同一收集栈，两条路径等价。
- **SDK/CE 版本独立演进**：SDK 从"与 CE 版本同步"改为独立版本。CE 在 `pyproject.toml` 里以 **PyPI 固定版本**依赖它（`qingci-plugin-sdk==X.Y.Z`，自 SDK 1.15.2 起由官方 PyPI 分发），`uv.lock` 记录版本、wheel URL 与 SHA256。CE 发版前需确认 SDK 依赖已指向目标版本（SDK 破坏性变更不被旧 CE 拉到）。

## 4. 命名约定（结构层面）

| 对象 | 约定 | 示例 |
|------|------|------|
| Python 包/模块 | 小写下划线 `snake_case` | `event_bus.py`, `session_state.py` |
| 路由文件 | 按资源命名 | `routes/plugin.py` |
| 内置插件目录 | 小写单词 | `builtin/chat/` |
| 外部插件目录 | 小写单词；模板加 `_` 前缀 | `hello/`, `_template/` |
| 测试文件 | `test_<被测模块>.py` | `test_plugin_manager.py` |
| 前端目录 | 语义化子目录 | `views/`, `components/`, `stores/`, `composables/` |

> 详细命名与编码约定见 [CODING_STANDARDS.md](CODING_STANDARDS.md)。
