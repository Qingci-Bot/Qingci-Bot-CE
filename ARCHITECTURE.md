# Qingci-Bot CE 架构

## 系统架构

```
┌──────────┐   OneBot 11 WS   ┌──────────────────────────────────────────┐   HTTP/WS   ┌──────────┐
│  LLBot   │ ◄──────────────► │            Qingci-Bot CE                │ ◄─────────► │  Web UI  │
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

1. **LLBot**（QQ 协议端）通过 OneBot 11 反向 WebSocket 连接至 Qingci-Bot CE
2. **aiocqhttp** 解析事件，分发至 **Dispatcher**
3. **Dispatcher** 按 priority 调度 **PluginManager** 中的 Matcher，匹配 Rule/Permission 后执行 handler
4. 未匹配则回退到旧式 `on_message`；内置 chat 插件调用 **LLMManager** 生成回复
5. 回复通过 OneBot 连接发送回 LLBot，最终到达 QQ 用户
6. **Web UI** 通过 HTTP/WebSocket 与 API 服务通信，管理配置、插件、日志等

## 项目结构

```
Qingci-Bot-CE/
├── main.py                    # 统一入口
├── pyproject.toml
├── alembic.ini                # Alembic 迁移配置
├── config.example.yaml        # 配置模板（脱敏，复制为 config.yaml）
├── config.yaml                # 配置文件（首次运行自动生成，已被 .gitignore 忽略）
├── build.ps1                  # PyInstaller 打包脚本
├── qingci-bot-ce.spec         # PyInstaller 打包配置
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
│   │   ├── logformat.py       # 结构化 JSON 日志
│   │   ├── session_state.py   # 会话状态管理（TTL 键值存储）
│   │   ├── event_bus.py       # 跨插件事件总线（发布-订阅）
│   │   └── di.py              # 依赖注入容器（SINGLETON/TRANSIENT/SCOPED）
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
│   ├── testing/               # 插件测试工具（TestBot + 事件构造器，无需启动真实 Bot）
│   │   ├── bot.py             # TestBot 轻量测试环境
│   │   └── events.py          # OneBot v11 事件构造器
│   └── plugin/
│       ├── base.py            # 插件基类（支持 matchers 属性、生命周期钩子、i18n/data_dir）
│       ├── manager.py         # 插件管理器（热加载 + 模块级收集）
│       ├── matcher.py         # Matcher + MatcherContext + 工厂函数（on_command 等）
│       ├── rule.py            # 规则系统（startswith/command/subcommand/regex 等）
│       ├── permission.py      # 权限系统（SUPERUSER/PRIVATE/GROUP 等）
│       ├── ratelimit.py       # RateLimiter 限流
│       ├── llm_tool.py        # @llm_tool 插件级 LLM 工具声明
│       ├── watcher.py         # 插件自动热重载监听
│       └── builtin/           # 内置插件（目录结构）
│           ├── chat/          # LLM 对话（Matcher API）
│           ├── admin/         # 管理命令（含 /filter /group）
│           ├── help/          # /help 命令（按权限列出可用命令）
│           ├── imagegen/      # AI 绘图（/image 命令）
│           └── knowledge/     # 知识库管理（/kb 命令）
├── plugins/                   # 外部插件目录（Bot 启动时自动扫描加载）
│   ├── __init__.py
│   ├── _template/             # 插件开发模板（以 _ 开头，不自动加载）
│   │   ├── __init__.py
│   │   └── plugin.json        # 插件元数据模板
│   └── hello/                 # 最小示例插件
│       └── __init__.py
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
| 插件系统 | Matcher + Rule + Permission + require/export + 中间件 + 指标监控 (借鉴 NoneBot2) |
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

### 向量检索（RAG）初始化

向量检索模式基于 LanceDB（嵌入式向量数据库）+ litellm embedding，无需额外服务端部署，但需要完成以下初始化步骤：

#### 1. 启用向量检索模式

在 `config.yaml` 中配置 `rag` 节：

```yaml
rag:
  enabled: true
  mode: vector                        # 从 keyword 切换到 vector
  embedding_model: "text-embedding-3-small"  # OpenAI 兼容的 embedding 模型
  embedding_api_key: ""               # 留空则复用 llm.api_key
  embedding_api_url: ""               # 留空则复用 llm.api_url
  knowledge_dir: "data/knowledge"     # 知识库文档目录
  top_k: 3                            # 检索返回的最相关条目数
  chunk_size: 400                     # 文档分块大小（字符）
  chunk_overlap: 50                   # 相邻分块重叠字符数
  collection_name: "qingci_knowledge" # LanceDB 集合名
```

> **embedding 模型选择**：使用 OpenAI 兼容的 embedding API（如 `text-embedding-3-small`、`bge-large-zh` 等）。Ollama 本地模型需在 `embedding_api_url` 中指定地址（如 `http://localhost:11434/v1`）。

#### 2. 准备知识库文档

将 `.txt` 或 `.md` 文件放入 `data/knowledge/` 目录。支持中文、英文混合文档，文件命名建议使用中文标题（如 `产品手册.txt`）。

#### 3. 生成 Embedding 并建立索引

索引生成是**全自动**的，无需手动执行脚本：

- **启动时自动索引**：Bot 启动时若 `mode: vector`，自动扫描 `knowledge_dir` 下所有文档，调用 embedding API 生成向量并写入 LanceDB
- **文档变更自动重建**：通过 Web UI 知识库管理页或 `/kb` 命令添加/删除文档时，自动触发全量索引重建
- **手动触发重建**：在 Web UI 知识库管理页点击「重建索引」按钮

**首次索引耗时估算**：取决于文档总量和 embedding API 响应速度。100 篇中等长度文档约需 30-60 秒，期间 Bot 其他功能不受影响。

#### 4. 索引存储位置

LanceDB 数据存储在 `data/knowledge/.lancedb/` 目录下，无需额外维护。如需迁移或备份，直接复制整个 `.lancedb/` 目录即可。

#### 5. 验证索引状态

通过 Web UI 知识库管理页可查看：文档列表、各文档分块数、索引更新时间。或调用 API：

```bash
curl http://localhost:8000/api/knowledge/documents
```

#### 注意事项

- **embedding API 费用**：每次全量索引重建会调用 embedding API，文档量大时请注意 API 调用成本。日常增量添加文档也会触发全量重建（当前版本未实现增量索引）
- **embedding 模型一致性**：索引重建时使用的 embedding 模型必须与检索时一致，否则向量维度不匹配会导致检索失败
- **降级到 keyword 模式**：若 embedding API 不可用，可将 `mode` 改回 `keyword`，立即回退到关键词检索模式，无需重建索引

### 数据库迁移至 PostgreSQL

默认使用 SQLite + WAL 模式，适合小规模部署（&lt;10 个活跃群）。当面对数百个高频群聊时，SQLite 的串行写锁可能成为性能瓶颈。

**方式一：使用迁移脚本（推荐）**

项目提供了 `scripts/migrate_sqlite_to_pg.py` 工具脚本，可一键将 SQLite 数据迁移至 PostgreSQL：

```bash
# 1. 安装 PostgreSQL 驱动
uv pip install asyncpg --python .venv\Scripts\python.exe

# 2. 创建目标数据库（在 psql 中执行）
# CREATE DATABASE qingci_bot;

# 3. 运行迁移脚本
python scripts/migrate_sqlite_to_pg.py \
  --pg-url "postgresql+asyncpg://user:password@localhost:5432/qingci_bot"
```

脚本会自动：
- 读取 SQLite 中所有表数据
- 在 PostgreSQL 中创建表结构
- 逐表迁移数据（messages / sessions / plugin_configs / group_configs / usage_logs / audit_logs）
- 输出迁移进度与统计

**方式二：手动迁移**

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

> 注意：PostgreSQL 的 `expire_on_commit=False` 配置与 SQLite 一致，无需改动。

### LLBot 断连恢复

Qingci-Bot CE 启动后会自动监测 LLBot 的 WebSocket 连接状态。若 LLBot 异常退出或重启：

- **自动重连**：检测到断连后，以指数退避策略（初始 1s，上限 60s）自动尝试重连，无需手动干预
- **优雅降级**：断连期间 Web UI 与 API 服务正常可用，Bot 状态显示为"未连接"
- **重连成功**：自动恢复消息收发，已加载插件与配置保持不变

断连监控与重连回调内置于 `bot/core/connection.py`，无需额外配置。