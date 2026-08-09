# Qingci-Bot 架构

## 系统架构

```
┌──────────┐   OneBot 11 WS   ┌──────────────────────────────────────────┐   HTTP/WS   ┌──────────┐
│  LLBot   │ ◄──────────────► │            Qingci-Bot                   │ ◄─────────► │  Web UI  │
│ (协议层)  │  收发消息/事件    │  ┌──────────────────────────────────┐   │   API 推送   │  (管理端)  │
└──────────┘                  │  │ aiocqhttp (反向 WS 服务端)        │   │            └──────────┘
                              │  ├──────────────────────────────────┤   │
                              │  │ Dispatcher (Matcher/Rule/Perm)   │   │
                              │  ├──────────────────────────────────┤   │
                              │  │ PluginManager (热加载/双轨调度)    │   │
                              │  ├──────────────────────────────────┤   │
                              │  │ LLMManager (litellm 多提供商)     │   │
                              │  ├──────────────────────────────────┤   │
                              │  │ Database (SQLModel + Alembic)    │   │
                              │  └──────────────────────────────────┘   │
                              └──────────────────────────────────────────┘
```

### 数据流

1. **LLBot**（QQ 协议端）通过 OneBot 11 反向 WebSocket 连接至 Qingci-Bot
2. **aiocqhttp** 解析事件，分发至 **Dispatcher**
3. **Dispatcher** 按 priority 调度 **PluginManager** 中的 Matcher，匹配 Rule/Permission 后执行 handler
4. 未匹配则回退到旧式 `on_message`；内置 chat 插件调用 **LLMManager** 生成回复
5. 回复通过 OneBot 连接发送回 LLBot，最终到达 QQ 用户
6. **Web UI** 通过 HTTP/WebSocket 与 API 服务通信，管理配置、插件、日志等

## 项目结构

```
Qingci-Bot/
├── main.py                    # 统一入口
├── pyproject.toml
├── alembic.ini                # Alembic 迁移配置
├── config.example.yaml        # 配置模板（脱敏，复制为 config.yaml）
├── config.yaml                # 配置文件（首次运行自动生成，已被 .gitignore 忽略）
├── build.ps1                  # PyInstaller 打包脚本
├── qingci-bot.spec            # PyInstaller 打包配置
├── bot/
│   ├── config.py              # 配置管理（Pydantic 模型）
│   ├── core/
│   │   ├── bot.py             # Bot 主类（生命周期、事件调度、全局钩子）
│   │   ├── connection.py      # OneBot 连接（aiocqhttp 反向 WS）
│   │   ├── dispatcher.py      # 消息分发 + Matcher 调度
│   │   ├── message.py         # 类型化消息构造器（Message/MessageSegment）
│   │   ├── broadcast.py       # 消息广播
│   │   ├── filter.py          # 敏感词过滤器
│   │   ├── scheduler.py       # 定时任务调度器
│   │   ├── tasks.py           # 后台任务管理（防 GC + 停机等待）
│   │   ├── alerter.py         # 错误告警器
│   │   └── logformat.py       # 结构化 JSON 日志
│   ├── llm/
│   │   ├── adapter.py         # LLM 适配器基类（支持 tools/images）
│   │   ├── litellm_adapter.py # litellm 实现（100+ 提供商）
│   │   ├── manager.py         # 会话管理 + Token 裁剪 + 摘要 + 持久化
│   │   ├── tools.py           # Function Calling 工具注册表
│   │   └── mcp.py             # MCP 服务器接入（stdio/HTTP）
│   ├── rag/
│   │   └── knowledge.py       # 知识库（keyword 关键词检索 + LanceDB 向量检索）
│   ├── db/
│   │   ├── database.py        # 数据库仓储（基于 SQLModel）
│   │   ├── engine.py          # 异步引擎 + 会话工厂（WAL 模式）
│   │   └── models.py          # SQLModel 模型定义
│   └── plugin/
│       ├── base.py            # 插件基类（支持 matchers 属性）
│       ├── manager.py         # 插件管理器（热加载 + 模块级收集）
│       ├── matcher.py         # Matcher + MatcherContext + 工厂函数
│       ├── rule.py            # 规则系统（startswith/command/regex 等）
│       ├── permission.py      # 权限系统（SUPERUSER/PRIVATE/GROUP 等）
│       └── builtin/           # 内置插件
│           ├── chat.py        # LLM 对话（Matcher API）
│           ├── admin.py       # 管理命令（含 /filter /group）
│           ├── help.py        # /help 命令（按权限列出可用命令）
│           ├── imagegen.py    # AI 绘图（/image 命令）
│           └── knowledge.py   # 知识库管理（/kb 命令）
├── plugins/                   # 外部插件目录（Bot 启动时自动扫描加载）
│   ├── __init__.py
│   ├── _template.py           # 插件开发模板（以 _ 开头，不自动加载）
│   └── hello.py               # 最小示例插件
├── migrations/                # Alembic 迁移脚本
│   ├── env.py                 # 异步迁移环境
│   └── versions/              # 迁移版本
├── api/
│   ├── auth.py                # API 鉴权
│   ├── audit.py               # 审计日志（埋点 + 查询）
│   ├── server.py              # FastAPI 应用
│   └── routes/                # API 路由（bot/config/plugin/log/group/auth/backup）
├── web/                       # Vue 3 前端
│   └── src/
│       ├── views/             # 页面组件
│       ├── stores/            # Pinia 状态管理
│       ├── router/            # 路由配置
│       └── styles/            # 全局样式
├── desktop/
│   ├── main.py                # 桌面入口
│   ├── tray.py                # 系统托盘
│   └── app-icon.ico           # 应用图标（exe 图标 + 托盘图标）
└── data/
    └── qingci-bot.db          # SQLite 数据库文件
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.12 + FastAPI + uvicorn |
| QQ 协议 | aiocqhttp (OneBot 11 反向 WS) |
| LLM | litellm (统一接口，延迟导入加速启动) |
| MCP | mcp (Model Context Protocol，stdio/HTTP) |
| 数据库 | SQLModel + Alembic + aiosqlite (WAL 模式) |
| 定时任务 | APScheduler |
| 插件系统 | Matcher + Rule + Permission + require (借鉴 NoneBot2) |
| 前端 | Vue 3 + Vite + Pinia |
| 桌面 | PyWebView + pystray |

## 核心模块

### Bot 主类 (`bot/core/bot.py`)

生命周期管理、事件调度、全局钩子（前置/后置中间件）、事件并发限流（Semaphore）。

### Dispatcher (`bot/core/dispatcher.py`)

- 收集所有已注册 Matcher，按 priority 升序排序
- 依次检查 Rule + Permission，命中则执行 handler
- 无 Matcher 匹配时回退到旧式 `on_message`
- 支持 block 控制是否继续后续 Matcher

### PluginManager (`bot/plugin/manager.py`)

- 插件热加载/卸载/重载（"先建后拆"策略）
- 模块级装饰器自动收集（`on_command` 等）
- 依赖解析（require），循环依赖检测
- 路径白名单：仅允许 `plugins.*` 和 `bot.plugin.builtin.*`

### LLMManager (`bot/llm/manager.py`)

- 会话管理：按群聊/用户独立维护对话历史，内存 + 数据库双写
- Token 裁剪：按条数与 Token 双重限制，超出自动裁剪
- 会话摘要：可选的摘要压缩，保留最近 N 轮原文
- 人格切换：`/persona` 命令会话级覆盖 system_prompt

### Database (`bot/db/`)

- SQLModel 模型定义 + Alembic 迁移管理
- aiosqlite 异步引擎（WAL 模式，提升并发读性能）
- 批量写入优化（`save_messages_batch`）
- 在线备份（sqlite backup API）

## 生产环境部署

### 数据库迁移至 PostgreSQL

默认使用 SQLite + WAL 模式，适合小规模部署（&lt;10 个活跃群）。当面对数百个高频群聊时，SQLite 的串行写锁可能成为性能瓶颈。

**迁移至 PostgreSQL**（推荐生产环境）：

SQLAlchemy 异步已原生支持 PostgreSQL，迁移成本较低：

1. 安装驱动：

```bash
uv pip install asyncpg --python .venv\Scripts\python.exe
```

2. 修改 `bot/db/engine.py` 中的连接串：

```python
# 替换 SQLite 连接串为 PostgreSQL
DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/qingci_bot"
engine = create_async_engine(DATABASE_URL, echo=False)
```

3. 运行 Alembic 迁移重建表结构：

```bash
alembic upgrade head
```

4. 移除 SQLite 专用 PRAGMA 设置（`_set_sqlite_pragma` 函数与 `event.listen` 调用）。

数据迁移：可使用 `bot/db/database.py` 的 `backup_database()` 导出 SQLite 数据为 JSON，再导入 PostgreSQL。

> 注意：PostgreSQL 的 `expire_on_commit=False` 配置与 SQLite 一致，无需改动。

### LLBot 断连恢复

Qingci-Bot 启动后会自动监测 LLBot 的 WebSocket 连接状态。若 LLBot 异常退出或重启：

- **自动重连**：检测到断连后，以指数退避策略（初始 1s，上限 60s）自动尝试重连，无需手动干预
- **优雅降级**：断连期间 Web UI 与 API 服务正常可用，Bot 状态显示为"未连接"
- **重连成功**：自动恢复消息收发，已加载插件与配置保持不变

相关配置见 `config.yaml` 的 `onebot.reconnect` 节（可选，默认启用）。