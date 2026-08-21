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
| 前端依赖/构建 | `web/node_modules/`、`web/dist/` | 根目录 | 已忽略 |
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
│   └── routes/             # REST 路由：login/backup/bot/command/config/group/instances/log/market/plugin
├── bot/                    # Bot 核心逻辑（纯 Python 包）
│   ├── core/               # 框架层（生命周期与调度）：bot/composition/dispatcher/event_bus/
│   │                       #   di/scheduler/session_state/tasks/message(仅 v11 兼容)/
│   │                       #   v11_compat（v11→v12 事件翻译）/platforms（含 onebot11/onebot12/telegram）
│   │   ├── composition.py  # 组合根：assemble_bot() 组件装配 + DI 注册（__init__ 不再手写装配）
│   │   ├── bot.py          # Bot 主类；get_bot() 经 DI 容器解析（resolve_sync），无模块级单例
│   │   ├── dispatcher.py   # 消息分发 + Matcher 调度（MessageContext 引 protocol.context）
│   │   └── platforms/      # 多平台适配器：base.py（PlatformAdapter 契约）+ onebot11/onebot12/telegram
│   │                       #   OneBotConnection 实现契约作为「onebot」平台；回复按来源平台路由
│   ├── alerter.py          # 错误告警器（ERROR 日志阈值 → 私聊通知管理员）
│   ├── filter.py           # 敏感词过滤器（词库 + 打码）
│   ├── broadcast.py        # 消息广播（WS 实时推送 broker）
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
│   │   ├── manager.py      # 插件加载/卸载/依赖/元数据 + SDK data_root 实例重定向
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
│   │   ├── database.py     # Database 会话/仓储
│   │   ├── engine.py       # 数据库引擎
│   │   └── models.py       # ORM 模型
│   ├── rag/                # 知识库检索（关键词 + 向量；向量需可选依赖 lancedb）
│   │   └── knowledge.py    # KnowledgeStore（lancedb 缺失时 vector 模式自动回退 keyword）
│   ├── testing/            # TestBot 测试沙箱
│   │   ├── bot.py          # TestBot
│   │   └── events.py       # 事件工厂
│   ├── config.py           # ConfigManager（config.yaml 加载与校验）
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
├── desktop/                # 桌面应用壳（app/splash/tray/single_instance/relaunch + 图标资源）
├── plugins/                # 外部插件目录（运行时加载；实例模式下为 instances/<name>/plugins）
│   ├── _template/          # 插件模板（下划线前缀 = 非正式/模板，不参与加载）
│   │   └── plugin.json     # 插件元数据模板
│   └── hello/              # 示例插件
├── instances/              # 实例注册表（运行时生成，启动必需；每个实例一个自包含目录，无全局模式）
├── migrations/             # Alembic 数据库迁移
│   └── versions/           # 版本迁移脚本
├── tests/                  # pytest 测试
│   ├── test_*.py           # 按被测模块命名
│   └── plugin_pkg/         # 测试用插件包（dep/di/p1/p2 等）
├── scripts/                # 一次性/运维脚本（如 SQLite→PostgreSQL 迁移）
├── docs/                   # 规范文档（本文档 + CODING_STANDARDS.md）
├── build.ps1               # Windows 构建脚本（打包前 -e 安装 Plugins-SDK）
├── qingci-bot-ce.spec      # PyInstaller 打包配置（collect_all 打包 SDK）
├── pyproject.toml          # 依赖（含 git 依赖 qingci-plugin-sdk）、ruff/mypy/pytest 配置
├── alembic.ini             # 迁移配置
├── .pre-commit-config.yaml # pre-commit 钩子
└── README.md / ARCHITECTURE.md / PLUGIN_DEV.md / CONTRIBUTING.md / CHANGELOG.md / SECURITY.md / LICENSE
```

## 3. 各目录职责与归属原则

| 目录 | 职责边界 | 禁止放入 |
|------|----------|----------|
| `api/` | 对外 HTTP 接口、鉴权、审计 | 业务逻辑（应下沉到 `bot/`） |
| `bot/core/` | **框架层**：生命周期、连接、调度、DI、组合根装配、事件总线、会话状态 | 具体业务与功能组件 |
| `bot/`（根级） | 功能/横切组件：alerter / filter / broadcast / logformat / html_renderer | 框架调度核心 |
| `bot/plugin/` | 插件系统机制（`protocol/` 协议层薄转发 SDK + 顶层兼容再导出 + 运行时 manager/market 等）+ `builtin/` 内置插件 | 框架强耦合的临时代码 |
| `bot/llm|db|rag` | 领域能力模块 | 与框架调度耦合的逻辑 |
| `web/` | 前端 UI | 后端逻辑 |
| `tests/` | 所有测试，按 `test_<模块>.py` 命名 | 生产代码 |
| `scripts/` | 一次性工具脚本 | 可被 `main.py`/`__init__` 覆盖的频繁调用逻辑 |

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
