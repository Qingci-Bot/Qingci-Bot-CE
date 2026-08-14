# 项目结构规范

> 本文档定义 Qingci-Bot-CE 的目录职责、文件组织与产物归属约定，是结构相关变更的准绳。

## 1. 仓库定位

`Qingci-Bot-CE` 是 `Qingci-Bot` 根目录下的**多个独立子项目之一**。根目录还包含其他子项目（如 `Plugins-SDK`、`qqbot-plugin-comparison`），因此：

- 每个子项目自己管理自己的 `.git` 仓库与依赖；
- **任何子项目的运行产物（缓存、构建产物等）不得写入根目录**，必须留在自身目录内。

### 产物归属约定

| 产物类型 | 允许位置 | 禁止位置 | 忽略规则 |
|----------|----------|----------|----------|
| Python 缓存 | `Qingci-Bot-CE/.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`__pycache__/` | 根目录或其他子项目 | `.gitignore` 已忽略 |
| 覆盖率 | `Qingci-Bot-CE/.coverage`、`htmlcov/` | 根目录 | 已忽略 |
| 前端依赖/构建 | `web/node_modules/`、`web/dist/` | 根目录 | 已忽略 |
| 虚拟环境 | `Qingci-Bot-CE/.venv/` | 根目录 | 已忽略 |
| 安装元数据 | `*.egg-info/` | 根目录 | 已忽略 |
| 运行时数据 | `data/`、`*.db`、`config.yaml` | 根目录 | 已忽略 |

> 规则：**在 `Qingci-Bot-CE/` 目录下运行所有命令**（ruff、pytest、mypy、构建），使缓存落在本目录内，避免污染根目录。

## 2. 目录结构总览

```
Qingci-Bot-CE/
├── main.py                 # 后端入口（启动 Bot + API）
├── api/                    # FastAPI 接口层
│   ├── server.py           # 应用装配、路由挂载、中间件
│   ├── auth.py             # 鉴权 / 审计横切逻辑
│   ├── audit.py            # 登录/操作审计
│   └── routes/             # REST 路由：auth/backup/bot/command/config/group/log/plugin
├── bot/                    # Bot 核心逻辑（纯 Python 包）
│   ├── core/               # 生命周期与调度：bot/connection/dispatcher/event_bus/di/
│   │                       #   scheduler/session_state/filter/alerter/tasks/broadcast/message/logformat
│   ├── plugin/             # 插件系统
│   │   ├── base.py         # PluginBase 基类
│   │   ├── manager.py      # 插件加载/卸载/依赖/元数据
│   │   ├── matcher.py      # Matcher 与匹配器工厂
│   │   ├── rule.py / permission.py / llm_tool.py / watcher.py / i18n 等
│   │   └── builtin/        # 内置插件：admin/chat/help/imagegen/knowledge
│   ├── llm/                # LLM 管理、适配器、工具调用（Function Calling / MCP）
│   ├── db/                 # SQLModel ORM、仓储、会话管理
│   ├── rag/                # 知识库检索（关键词 + 向量）
│   ├── testing/            # TestBot 测试沙箱（events / bot）
│   ├── config.py           # ConfigManager（config.yaml 加载与校验）
│   ├── i18n.py             # 国际化翻译器
│   ├── paths.py            # 路径解析（app_root / data_dir 等）
│   └── __init__.py
├── web/                    # Vue 3 前端
│   └── src/
│       ├── views/          # 页面级组件
│       ├── stores/         # Pinia 状态
│       ├── router/         # 路由
│       ├── composables/    # 组合式函数
│       └── styles/         # 全局样式
├── desktop/                # 桌面应用壳（main/splash/tray + 图标资源）
├── plugins/                # 外部插件目录（运行时加载）
│   ├── _template/          # 插件模板（下划线前缀 = 非正式/模板，不参与加载）
│   └── hello/              # 示例插件
├── migrations/             # Alembic 数据库迁移
├── tests/                  # pytest 测试
│   └── plugin_pkg/         # 测试用插件包
├── scripts/                # 一次性/运维脚本（如 SQLite→PostgreSQL 迁移）
├── docs/                   # 规范文档（本文档 + CODING_STANDARDS.md）
├── build.ps1               # Windows 构建脚本
├── qingci-bot-ce.spec      # PyInstaller 打包配置
├── pyproject.toml          # 依赖、ruff/mypy/pytest 配置
├── alembic.ini             # 迁移配置
├── .pre-commit-config.yaml # pre-commit 钩子
└── README.md / ARCHITECTURE.md / PLUGIN_DEV.md / CONTRIBUTING.md / CHANGELOG.md / SECURITY.md
```

## 3. 各目录职责与归属原则

| 目录 | 职责边界 | 禁止放入 |
|------|----------|----------|
| `api/` | 对外 HTTP 接口、鉴权、审计 | 业务逻辑（应下沉到 `bot/`） |
| `bot/core/` | 生命周期、连接、调度、DI、事件总线等**框架层** | 具体业务功能 |
| `bot/plugin/` | 插件系统机制 + `builtin/` 内置插件 | 框架强耦合的临时代码 |
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

## 4. 命名约定（结构层面）

| 对象 | 约定 | 示例 |
|------|------|------|
| Python 包/模块 | 小写下划线 `snake_case` | `event_bus.py`, `session_state.py` |
| 路由文件 | 按资源命名 | `routes/plugin.py` |
| 内置插件目录 | 小写单词 | `builtin/chat/` |
| 外部插件目录 | 小写单词；模板加 `_` 前缀 | `hello/`, `_template/` |
| 测试文件 | `test_<被测模块>.py` | `test_plugin_manager.py` |
| 前端目录 | 语义化子目录 | `views/`, `stores/`, `composables/` |

> 详细命名与编码约定见 [CODING_STANDARDS.md](CODING_STANDARDS.md)。