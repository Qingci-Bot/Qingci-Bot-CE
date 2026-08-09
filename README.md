# Qingci-Bot

基于 Python 的 QQ 机器人框架，对接 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot)（OneBot 11 协议），支持 LLM 智能对话、Web UI 和桌面应用。

## 架构

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

## 特性

- **OneBot 11 反向 WebSocket**：基于 [aiocqhttp](https://github.com/nonebot/aiocqhttp)，完整支持 OneBot v11 协议（消息段解析、API 调用、事件总线）
- **LLM 统一接口**：基于 [litellm](https://github.com/BerriAI/litellm)，支持 7 大提供商（OpenAI / DeepSeek / Ollama / SiliconFlow / Claude / Gemini / 自定义），含流式响应、Function Calling、多模态；填好 API Key 后可一键拉取提供商可用模型列表
- **人格/人设系统**：可配置多组人格（system_prompt 集合），聊天中 `/persona` 命令随时切换（会话级覆盖），Web UI 可视化管理
- **会话上下文管理**：按群聊/用户独立维护对话历史，内存 + 数据库双写持久化，按条数与 Token 双重裁剪（可选摘要压缩）；Web UI 按会话分组可视化查看 / 删除
- **插件系统**：借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持优先级、权限控制、命令注册器、插件间依赖声明（require），向后兼容旧式 `on_message`
- **安全与运维**：API Key 鉴权（登录防暴力限流）、敏感词过滤、对话限流、登录审计、数据库在线备份、错误告警、结构化 JSON 日志（可选）
- **增强能力**：AI 图片生成、轻量知识库（文件型 RAG）、会话摘要（历史裁剪）、Function Calling、MCP 服务器接入、定时任务调度器、LLM 用量统计
- **数据库 ORM**：SQLModel 模型定义 + Alembic 迁移管理，异步会话（aiosqlite + WAL 模式），支持在线备份与消息 CSV 导出
- **Web UI**：原神风格暗色主题，登录页 / 仪表盘（用量图表）/ LLM 配置（提供商联动 + 模型列表 + 人格 + MCP 管理）/ 对话调试台（流式聊天测试）/ 群配置 / 插件管理 / 消息日志（消息流 + 会话记录）/ 登录审计 / 系统设置
- **桌面应用**：PyWebView 套壳 + 系统托盘（关闭窗口自动驻留后台），开机自启
- **离线可用**：前端资源本地打包，无外部 CDN 依赖；litellm 延迟导入，启动不加载重型依赖

---

# 使用指南

## 环境要求

- Python 3.10+（推荐 3.12）
- [LLBot](https://github.com/LLOneBot/LuckyLilliaBot)（QQ 协议端）
- Node.js 18+（仅构建 Web UI 时需要，`web/dist` 已存在可跳过）
- 桌面模式额外依赖系统 WebView2 运行时（见「打包为 exe」注意事项）

## 1. 安装

```bash
# 创建虚拟环境
uv venv --python python3.12 .venv

# 安装全部依赖（推荐：pyproject.toml 已声明所有依赖，含桌面与 MCP）
uv pip install -e . --python .venv\Scripts\python.exe
```

> 若跳过 `pyproject.toml`，可手动安装核心依赖（桌面/MCP 可选）：
>
> ```bash
> uv pip install fastapi "uvicorn[standard]" websockets aiocqhttp aiosqlite \
>   sqlmodel alembic "sqlalchemy[asyncio]" litellm pydantic pyyaml httpx \
>   "apscheduler>=3.10,<4" "mcp>=1.6,<2" \
>   pywebview pystray pillow \
>   --python .venv\Scripts\python.exe
> ```

## 2. 启动

```bash
# 仅 API + Web UI（不启动 Bot）
.venv\Scripts\python main.py --no-bot

# 完整启动 Bot + API
.venv\Scripts\python main.py

# 自定义端口
.venv\Scripts\python main.py --port 9000

# 桌面应用
.venv\Scripts\python main.py --desktop
```

启动后访问 `http://127.0.0.1:8080/ui` 进入管理界面。

> **Web UI 未构建时**：若 `web/dist` 缺失或不完整，访问 `/` 会返回构建提示页（引导在 `web/` 目录执行 `npm install` 与 `npm run build`），API 服务本身仍正常可用。克隆仓库后首次启动前请先构建前端。

## 3. 配置 LLBot

在 LLBot 中添加反向 WebSocket 连接：

- 地址：`ws://127.0.0.1:3001/ws`（端口默认 3001，需与 `config.yaml` 的 `onebot.port` 保持一致；`config.example.yaml` 中示例为 8888，以你的实际配置为准）
- Access Token：留空或与 `config.yaml` 中 `onebot.access_token` 保持一致

LLBot 会自动携带 OneBot v11 标准的 `X-Client-Role: universal` 和 `X-Self-ID` header 连接。

## 4. 配置 LLM

在 Web UI 的「LLM 配置」页面填写 API 信息，或直接编辑 `config.yaml`：

```yaml
llm:
  provider: deepseek              # openai / deepseek / ollama / siliconflow / claude / gemini / custom
  api_url: https://api.deepseek.com/v1  # 留空则按 provider 直连官方
  api_key: sk-your-key
  model: deepseek-chat
  system_prompt: 你是一个友好的 QQ 机器人助手。
  max_context_tokens: 8192        # 上下文窗口 token 上限，超出自动裁剪历史
  timeout: 60                     # 单次 LLM 请求超时（秒）
  num_retries: 2                  # 请求失败重试次数
  enable_tools: true              # Function Calling 工具调用开关
  mcp_servers:                    # MCP 服务器列表（需 enable_tools）
    - name: filesystem            #   服务器名（工具名带 mcp_filesystem_ 前缀）
      command: npx                #   stdio 模式：子进程命令
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      url: ''                     #   HTTP 模式：填写后忽略 command
      env: {}                     #   可选额外环境变量
  personas:                       # 人格列表（/persona 命令切换，会话级覆盖）
    - name: 猫娘
      description: 可爱的猫娘
      system_prompt: 你是一只可爱的猫娘，喜欢用"喵"结尾。
    - name: 助手
      description: 严谨的助手
      system_prompt: 你是严谨的技术助手，回答简洁准确。
  default_persona: ''             # 默认人格名（空 = 使用 system_prompt）
```

**提供商联动与模型列表**：切换 `provider` 时，Web UI 会自动带出推荐的 `api_url` 与 `model`（预设见「LLM 配置」页），用户仍可覆盖为自定义值。填入 `api_key` 后，点击「获取模型」即可调用 `/api/config/llm/models` 向提供商查询可用模型列表并回填到下拉框（Ollama / Claude / Gemini / OpenAI 兼容协议均支持）。

LLM 连接测试（`/api/config/llm/test`）使用 10 秒短超时探测，不受 `timeout` 配置影响，并透传具体失败原因（鉴权 / 超时 / 网络 / 其他）。

**人格切换**：聊天中发送 `/persona 列表` 查看全部，`/persona 猫娘` 切换（仅对当前会话生效），`/persona 重置` 恢复默认人格或 `system_prompt`。

**MCP 工具**：开启 `enable_tools` 并配置 `mcp_servers` 后，启动时自动连接各服务器并将工具注册为 `mcp_{服务器名}_{工具名}` 供 LLM 调用。修改 MCP 配置后需重启 Bot 生效。

**provider 路由规则**（基于 litellm）：
- `api_url` 非空：统一走 OpenAI 兼容协议（`openai/{model}` + `api_base`），兼容任意 OpenAI 协议服务
- `api_url` 为空：按 provider 直连官方（`deepseek/{model}`、`ollama/{model}` 等）
- `provider: custom`：必须填 `api_url`，走 OpenAI 兼容协议

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--no-bot` | 仅启动 API 服务 | - |
| `--desktop` | 启动桌面应用 | - |
| `--port` | API 端口 | 8080 |
| `--host` | API 监听地址 | 127.0.0.1 |
| `--config` | 配置文件路径 | config.yaml |

## 管理命令

**管理员命令**（QQ 号在 `config.yaml` 的 `bot.admin_users` 中配置）：

| 命令 | 说明 |
|------|------|
| `/status` | 查看 Bot 运行状态（OneBot 连接 / LLM 可用性 / 消息记录数） |
| `/clear` | 清除当前会话历史 |
| `/blacklist add <QQ>` | 添加用户到黑名单 |
| `/blacklist remove <QQ>` | 从黑名单移除用户 |
| `/filter on\|off\|reload` | 敏感词过滤开关 / 重载词库（词库为空时会提示编辑 `data/sensitive_words.txt`） |
| `/group on\|off` | 当前群 Bot 开关 |
| `/kb add\|list\|search\|remove\|reload` | 知识库管理（需开启 `rag.enabled`） |

**所有用户可用命令**（无需管理员权限）：

| 命令 | 说明 |
|------|------|
| `/help`（或 `/帮助`） | 按当前用户权限列出可用命令 |
| `/image <提示词>`（或 `/画图`） | AI 绘图（需开启 `image.enabled`，成功后以图片消息回复） |
| `/persona` | 查看当前会话人格 |
| `/persona 列表` | 列出全部可用人格 |
| `/persona <名称>` | 切换当前会话人格（配置了 `llm.personas` 时可用） |
| `/persona 重置` | 恢复默认人格或 `system_prompt` |

## API 鉴权

在 `config.yaml` 中设置 `api_key` 字段启用 API 鉴权：

```yaml
api_key: your-secret-key
```

- 为空时**不启用鉴权**（仅本地开发推荐）
- 设置后，除以下免鉴权端点外，**所有接口**（含 GET 读操作）都需要携带 `X-API-Key` 请求头：
  - `GET /api/bot/status`、`GET /api/bot/health`（状态/健康检查）
  - `GET /api/auth/status`、`POST /api/auth/login`（登录与鉴权状态）
- 在 Web UI 的「系统设置」页面可同时配置服务端 Key 和浏览器端 Key
- WebSocket（`/api/ws/log`、`/api/ws/chat`）通过 `token` 查询参数鉴权，方式同上

## 配置文件说明

> 配置模板见 `config.example.yaml`（敏感字段已脱敏，复制为 `config.yaml` 后按需修改）。
> 下方为完整字段说明：

```yaml
bot:
  name: Qingci-Bot
  admin_users: [123456789]        # 管理员 QQ 号列表
  trigger_mode: at                 # 触发方式: at / keyword / always
  trigger_keywords: ["/bot", "/ai"] # keyword 模式的触发词
  group_blacklist: []              # 群黑名单
  user_blacklist: []               # 用户黑名单
  log_json: false                  # 结构化 JSON 日志（false 使用普通文本日志）
onebot:
  host: 127.0.0.1
  port: 3001                       # LLBot 连接 ws://host:port/ws
  access_token: ''
llm:
  provider: openai                 # openai / deepseek / ollama / siliconflow / claude / gemini / custom
  api_url: https://api.openai.com/v1  # 留空则按 provider 直连官方
  api_key: sk-xxx
  model: gpt-4o-mini
  max_tokens: 2048                 # 单次回复最大 token
  temperature: 0.7
  system_prompt: 你是一个友好的 QQ 机器人助手。
  max_history: 20                  # 最大对话历史轮数（每轮 = user + assistant）
  max_context_tokens: 8192         # 上下文窗口 token 上限，超出自动裁剪历史
  timeout: 60                      # 单次 LLM 请求超时（秒）
  num_retries: 2                   # LLM 请求失败重试次数
  enable_summary: false            # 会话摘要开关（与 session_summary.enabled 等价，任一为 true 即启用）
  enable_tools: false              # Function Calling 工具调用开关（默认关闭）
  max_tool_rounds: 5               # 工具调用最大轮次
  mcp_servers: []                  # MCP 服务器列表（enable_tools 开启后生效，见上方示例）
  personas: []                     # 人格列表（/persona 命令切换，见上方示例）
  default_persona: ''              # 默认人格名（空 = 使用 system_prompt）
rate_limit:
  enabled: false                   # 对话限流（默认关闭）
  daily_limit: 50                  # 每用户每日对话上限
  cooldown_seconds: 10             # 同一用户两次对话最小间隔（秒）
filter:
  enabled: false                   # 敏感词过滤（默认关闭）
  words_file: data/sensitive_words.txt  # 词库文件（一行一词，支持 # 注释）
  exempt_admins: true              # 管理员豁免
scheduler:
  enabled: true                    # 定时任务调度器（插件未注册任务时无副作用）
alert:
  enabled: false                   # 错误告警（默认关闭）
  error_threshold: 5               # 冷却窗口内 ERROR 日志条数阈值
  cooldown_minutes: 10             # 告警冷却时间（分钟）
image:
  enabled: false                   # 图片生成（默认关闭）
  model: dall-e-3
  api_url: ''                      # 留空则按 litellm 默认路由
  api_key: ''                      # 留空则回退 llm.api_key
rag:
  enabled: false                   # 轻量知识库（默认关闭，文件型关键词检索）
  embedding_model: ''              # 预留字段（当前为关键词检索，不使用向量）
  top_k: 3                         # 检索返回的最相关分块数
  knowledge_dir: data/knowledge    # 知识库目录（相对项目根目录）
  chunk_size: 400                  # 文档分块大小（字符数）
  chunk_overlap: 50                # 相邻分块重叠字符数
  max_inject_chars: 800            # 注入 system_prompt 的参考资料长度上限
session_summary:
  enabled: false                   # 会话摘要（默认关闭；与 llm.enable_summary 等价）
  keep_recent_turns: 3             # 摘要时保留最近 N 轮原文
  max_messages: 20                 # 触发摘要的条数阈值
  max_tokens: 4096                 # 触发摘要的 token 阈值
  summary_max_tokens: 512          # 摘要生成单次回复最大 token
log:
  usage_tracking: true             # LLM 用量入库（可退出的遥测；Dashboard 用量统计依赖该数据）
api_key: ''                        # API 鉴权密钥
```

## 进阶功能说明（功能开关均默认关闭）

| 配置节 | 功能 | 默认值 | 说明 |
|--------|------|--------|------|
| `rate_limit` | 对话限流 | `enabled: false` | 每用户每日对话上限 + 两次对话冷却间隔，超限回复提示 |
| `filter` | 敏感词过滤 | `enabled: false` | 词库为 `data/sensitive_words.txt`（一行一词，支持 `#` 注释）；词库为空时 `/filter` 命令与日志会明确提示；管理员可通过 `exempt_admins` 豁免 |
| `scheduler` | 定时任务调度器 | `enabled: true` | 调度器基座，由插件注册任务；无任务注册时零副作用 |
| `alert` | 错误告警 | `enabled: false` | 冷却窗口内 ERROR 日志达到 `error_threshold` 条时向管理员发消息告警，带 `cooldown_minutes` 冷却 |
| `image` | 图片生成 | `enabled: false` | `/image <提示词>`（或 `/画图`）命令；`image.api_key` 为空时回退 `llm.api_key`；成功后以 CQ 图片段回复 |
| `rag` | 轻量知识库 | `enabled: false` | 文件型关键词检索（纯 Python 无重型依赖）；开启后对话自动注入检索到的参考资料；`/kb` 命令管理文档（add/list/search/remove/reload） |
| `session_summary` | 会话摘要 | `enabled: false` | 与 `llm.enable_summary` 等价，任一为 true 即启用；上下文超过条数/token 阈值时将较早消息摘要压缩，保留最近 N 轮原文 |
| `log.usage_tracking` | LLM 用量入库 | `true` | 可退出的遥测：关闭后 chat/摘要/图片不再写 usage_logs，Dashboard 用量统计将为空 |
| `llm.enable_tools` | Function Calling | `false` | 启用工具调用（内置 `get_current_time` / `random_quote`，可经 ToolRegistry 扩展）；`max_tool_rounds` 限制最大轮次（默认 5） |
| `llm.personas` | 人格/人设 | `[]` | 多组 system_prompt；聊天中 `/persona` 切换（会话级覆盖）、`/persona 列表` 查看；Web UI「LLM 配置」管理 |
| `llm.mcp_servers` | MCP 服务器 | `[]` | 连接外部 MCP 服务器（stdio/HTTP 传输），工具注册为 `mcp_{服务器名}_{工具名}` 供 LLM 调用；需开启 `enable_tools`，修改后重启 Bot 生效 |
| `llm.provider` | 提供商联动 | `openai` | 切换 provider 自动带出预设 api_url/model（openai/deepseek/ollama/siliconflow/claude/gemini/custom 共 7 个）；`api_url` 非空统一走 OpenAI 兼容协议 |
| `llm.timeout` / `llm.num_retries` | 请求超时与重试 | `60` / `2` | 单次 LLM 请求超时秒数与失败重试次数 |
| `bot.log_json` | 结构化 JSON 日志 | `false` | 面向机器可读的日志采集场景 |

---

# 开发指南

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
│   │   └── knowledge.py       # 轻量知识库（文件型关键词检索）
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
│   └── tray.py                # 系统托盘
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

## 前端开发

```bash
cd web
npm install
npm run dev      # 开发模式（热更新，http://localhost:5173）
npm run build    # 构建生产版本到 web/dist/
```

开发模式下，前端请求会自动代理到 `http://127.0.0.1:8080`（在 `vite.config.js` 中配置）。

## Web UI 页面

| 页面 | 路由 | 说明 |
|------|------|------|
| 仪表盘 | `/` | 运行状态卡片 + LLM 用量统计图表（依赖 `log.usage_tracking`） |
| LLM 配置 | `/config` | 提供商切换（联动 api_url/model）、模型列表拉取、人格管理、MCP 服务器配置、连接测试 |
| 对话调试台 | `/lab` | 无需进入 QQ 即可流式测试 LLM 回复；独立会话 key，不污染真实对话（走 `/api/ws/chat`） |
| 群配置 | `/groups` | 各群 Bot 开关与触发模式 |
| 插件管理 | `/plugins` | 插件列表 / 详情 / 重载 / 加载外部插件 / 卸载 |
| 消息日志 | `/logs` | 实时消息流 + 会话记录可视化（按会话分组查看/删除，支持清理与 CSV 导出） |
| 系统设置 | `/settings` | 服务端/浏览器 API Key 配置 |
| 登录 | `/login` | API Key 登录（服务端已配置 `api_key` 时显示） |

## 插件开发

Qingci-Bot 插件系统借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持两种开发方式：

- **新式（推荐）**：用 `on_command`/`on_message` 等装饰器注册 Matcher，配合 Rule 规则匹配和 Permission 权限控制
- **旧式（兼容）**：重写 `on_message` 方法，返回回复文本

两种方式可共存，Dispatcher 按 priority 优先调度 Matcher，无匹配时回退到旧式 `on_message`。

### 插件基类

所有插件继承 `bot.plugin.base.PluginBase`：

```python
from bot.plugin.base import PluginBase

class MyPlugin(PluginBase):
    # 插件元信息（必填 name，其余可选）
    name = "my_plugin"
    version = "1.0.0"
    author = "YourName"
    description = "插件描述"
    require = []            # 依赖的其他插件 name 列表（加载前自动先加载，见下文）

    async def on_load(self):
        """插件加载时调用（必须实现）"""
        # 新式：在这里注册 Matcher
        # 旧式：可做初始化
        ...

    async def on_unload(self):
        """插件卸载时调用（必须实现）"""
        ...

    # 旧式消息处理（新式插件可省略或返回 None）
    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        return None
```

### 注入的依赖

插件加载后，以下属性由 `PluginManager` 自动注入：

| 属性 | 类型 | 说明 |
|------|------|------|
| `self.bot` | `QingciBot` | Bot 主实例 |
| `self.db` | `Database` | SQLite 数据库（基于 SQLModel） |
| `self.config` | `ConfigManager` | 配置管理器（可读写 `config.yaml`） |
| `self.connection` | `OneBotConnection` | OneBot 连接（可调用 QQ API） |
| `self.llm` | `LLMManager` | LLM 管理器（基于 litellm） |
| `self.scheduler` | `BotScheduler` | 定时任务调度器 |
| `self.tool_registry` | `ToolRegistry` | Function Calling 工具注册表 |
| `self.knowledge_store` | `KnowledgeStore` | 知识库（RAG 未启用时为 None） |
| `self.matchers` | `list[Matcher]` | Matcher 列表（在 `on_load` 中填充） |

### 插件依赖（require）

插件可声明依赖的其他插件，加载前 `PluginManager` 会自动先加载依赖：

```python
from bot.plugin.base import PluginBase

class MyPlugin(PluginBase):
    name = "my_plugin"
    require = ["admin"]   # 依赖 admin 插件（内置插件名或已加载插件名）

    async def on_load(self):
        # 通过 bot 获取依赖插件实例，调用其公开方法
        admin = self.bot.plugin_manager.get("admin")
        ...
```

- 依赖已注册则跳过；未注册时尝试加载 `bot.plugin.builtin.<name>` 模块
- 依赖缺失或形成循环依赖时插件加载失败（报错并保持旧插件生效）

### Matcher / Rule / Permission

**核心概念：**
- **Matcher**：绑定 handler + rule + permission + priority 的匹配单元
- **Rule**：消息匹配规则（前缀/命令/正则等），支持 `&`/`|`/`~` 组合
- **Permission**：权限检查（管理员/私聊/群聊等），支持 `&`/`|`/`~` 组合
- **MatcherContext**：增强版 MessageContext，额外注入 `bot`/`plugin`/`matcher` + `command`/`args`/`match`

**工厂函数：**

| 函数 | 说明 |
|------|------|
| `on_message(rule, permission, priority, block, temp)` | 通用消息匹配器 |
| `on_command(cmd, rule, permission, priority, block, temp)` | 命令匹配器（自动解析参数到 `ctx.args`） |
| `on_startswith(prefix, ...)` | 前缀匹配器 |
| `on_keyword(keywords, ...)` | 关键词匹配器 |
| `on_notice(rule, priority, block, temp)` | 通知事件匹配器 |
| `on_request(rule, priority, block, temp)` | 请求事件匹配器 |

**一次性匹配器（temp=True）**：匹配执行后自动从所属插件移除，适用于"等待下一次对话"等只应触发一次的场景，例如"输入数字确认操作"：

```python
self.matchers.append(
    on_command("confirm", temp=True)(self._confirm)
)
```

**内置 Rule：** `startswith` / `endswith` / `fullmatch` / `contains` / `regex` / `command` / `to_me` / `is_private` / `is_group` / `keyword` / `rate_limit`

**内置 Permission：** `EVERYONE` / `SUPERUSER` / `ADMIN` / `PRIVATE` / `GROUP` / `MEMBER` / `USER(ids)` / `GROUP_MEMBER(ids)`

### MatcherContext 字段

继承自 `MessageContext`，额外字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ctx.bot` | `QingciBot` | Bot 实例（供模块级 handler 访问依赖） |
| `ctx.plugin` | `PluginBase` | 当前插件实例 |
| `ctx.matcher` | `Matcher` | 当前匹配器 |
| `ctx.command` | `str` | 匹配到的命令名（`command` 规则写入） |
| `ctx.args` | `str` | 命令参数 / 前缀后的剩余文本 |
| `ctx.match` | `re.Match` | 正则匹配结果（`regex` 规则写入） |

基础字段（同 MessageContext）：`raw_event` / `message_type` / `message_id` / `user_id` / `group_id` / `self_id` / `plain_text` / `raw_message` / `is_at_bot` / `at_list` / `images` / `sender`

### 注册方式

**方式一：插件内注册（推荐，可访问 self）**

```python
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_command, MatcherContext
from bot.plugin.permission import SUPERUSER

class MyPlugin(PluginBase):
    name = "my_plugin"

    async def on_load(self):
        # 注册 Matcher，handler 为 self 的方法
        self.matchers.append(
            on_command("ping", permission=SUPERUSER)(self._handle_ping)
        )

    async def _handle_ping(self, ctx: MatcherContext) -> str:
        return "pong!"

    async def on_unload(self):
        pass
```

**方式二：模块级装饰器（PluginManager 自动收集）**

```python
from bot.plugin.matcher import on_command, MatcherContext

@on_command("ping")
async def ping_handler(ctx: MatcherContext) -> str:
    return "pong"
```

### 示例一：命令匹配 + 权限控制

```python
# plugins/greet.py
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_command, MatcherContext
from bot.plugin.permission import SUPERUSER

class GreetPlugin(PluginBase):
    name = "greet"
    version = "1.0.0"
    description = "问候插件（管理员专属）"

    async def on_load(self):
        # /greet <名字> -> 你好，<名字>！
        self.matchers.append(
            on_command("greet", permission=SUPERUSER)(self._greet)
        )

    async def on_unload(self):
        pass

    async def _greet(self, ctx: MatcherContext) -> str:
        name = ctx.args.strip() or "朋友"
        return f"你好，{name}！"
```

### 示例二：前缀匹配 + Rule 组合

```python
# plugins/translator.py
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_startswith, MatcherContext
from bot.plugin.rule import is_group  # 仅群聊触发

class TranslatorPlugin(PluginBase):
    name = "translator"
    version = "1.0.0"
    description = "中英互译（前缀 翻译 触发，仅群聊）"

    async def on_load(self):
        self.matchers.append(
            on_startswith("翻译", rule=is_group())(self._translate)
        )

    async def on_unload(self):
        pass

    async def _translate(self, ctx: MatcherContext) -> str:
        text = ctx.args.strip()  # startswith 规则自动去除前缀，剩余文本存入 args
        if not text:
            return "请输入要翻译的内容，如：翻译 hello"

        reply = await self.llm.chat(
            message=f"请将以下内容翻译为{'中文' if any(c.isascii() for c in text) else '英文'}：{text}",
            message_type=ctx.message_type,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
        )
        return reply
```

### 示例三：正则匹配 + 关键词

```python
# plugins/reminder.py
import re
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_message, MatcherContext
from bot.plugin.rule import regex, keyword

class ReminderPlugin(PluginBase):
    name = "reminder"
    version = "1.0.0"
    description = "提醒插件（正则提取时间）"

    async def on_load(self):
        # 匹配 "提醒我 X 点 Y 分"
        self.matchers.append(
            on_message(rule=regex(r"提醒我.*?(\d+)点(\d+)分"))(self._set_reminder)
        )
        # 匹配包含 "提醒" 关键词
        self.matchers.append(
            on_message(rule=keyword("提醒"))(self._hint)
        )

    async def on_unload(self):
        pass

    async def _set_reminder(self, ctx: MatcherContext) -> str:
        hour = ctx.match.group(1)
        minute = ctx.match.group(2)
        return f"已设置 {hour}:{minute} 的提醒"

    async def _hint(self, ctx: MatcherContext) -> str:
        return "格式：提醒我 X 点 Y 分"
```

### 示例四：通知事件处理

```python
# plugins/welcome.py
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_notice, MatcherContext
from bot.plugin.rule import Rule

class WelcomePlugin(PluginBase):
    name = "welcome"
    version = "1.0.0"
    description = "新人入群欢迎"

    async def on_load(self):
        self.matchers.append(
            on_notice()(self._on_group_increase)
        )

    async def on_unload(self):
        pass

    async def _on_group_increase(self, ctx: MatcherContext) -> None:
        event = ctx.raw_event
        if event.get("notice_type") == "group_increase":
            group_id = event.get("group_id")
            user_id = event.get("user_id")
            await self.connection.call_api(
                "send_group_msg",
                {
                    "group_id": group_id,
                    "message": f"[CQ:at,qq={user_id}] 欢迎加入本群！",
                },
            )
```

### 示例五：旧式 on_message（向后兼容）

旧式插件无需改动，继续工作：

```python
# plugins/pingpong.py
from typing import Optional
from bot.plugin.base import PluginBase
from bot.core.dispatcher import MessageContext

class PingPongPlugin(PluginBase):
    name = "pingpong"
    version = "1.0.0"
    description = "Ping-Pong 响应"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        if ctx.plain_text == "ping":
            return "pong!"
        return None
```

### 调度顺序

1. Bot 收到消息 → Dispatcher 解析为 `MessageContext`
2. 收集所有 Matcher，按 `priority` 升序排序（越小越先执行）
3. 依次检查每个 Matcher 的 `permission` 和 `rule`
4. 匹配成功则执行 `handler`：
   - 返回非 `None`（回复文本）→ 发送回复，**停止整个分发链**
   - 返回 `None` + `block=True` → 停止后续 Matcher
   - 返回 `None` + `block=False` → 继续下一个 Matcher
5. 所有 Matcher 都未匹配 → 回退到旧式 `on_message`（跳过已注册 Matcher 的插件）

### 优先级与阻塞控制

```python
# priority 越小越先执行（默认 1）
# block=True（默认）匹配后停止后续 Matcher
# block=False 允许后续 Matcher 继续匹配

@on_command("ping", priority=1, block=True)
async def ping(ctx: MatcherContext) -> str:
    return "pong"

@on_message(rule=keyword("天气"), priority=10, block=False)
async def weather_log(ctx: MatcherContext) -> None:
    # 仅记录，不回复，不阻塞后续处理
    await self.db.save_message(...)
```

> **兼容性说明**：此前实现与文档不一致（实际为大者优先），本次已修正为与文档一致的升序匹配。
> 内置插件中 admin（priority=1）先于 chat（priority=50）执行。
> 外部插件开发者若此前依赖旧的“大者优先”实际顺序，请自查 priority 配置。

### 消息构造（类型化 Message / CQ 码）

**推荐使用类型化消息构造器**（`bot/core/message.py`），自动处理 CQ 码转义：

```python
from bot.core.message import Message, MessageSegment

# 回复 + @ + 文本 + 图片 组合
msg = Message(
    MessageSegment.reply(ctx.message_id),
    MessageSegment.at(ctx.user_id),
    MessageSegment.text("请看这张图："),
    MessageSegment.image("https://example.com/img.png"),
)
await self.connection.send_msg("group", ctx.group_id, str(msg))
```

支持的消息段：`text` / `at` / `at_all` / `image` / `face` / `voice` / `video` / `reply` / `forward`。`Message.extract_plain_text()` 可提取纯文本。

**手动 CQ 码**（简单场景仍可用）：

| CQ 码 | 说明 |
|-------|------|
| `[CQ:at,qq=123456]` | @ 某人 |
| `[CQ:face,id=178]` | QQ 表情 |
| `[CQ:image,file=https://example.com/img.png]` | 图片 |
| `[CQ:reply,id=消息ID]` | 回复消息 |

```python
from bot.core.dispatcher import MessageDispatcher

at_code = MessageDispatcher.build_cq_at(ctx.user_id)
reply = f"{at_code} 收到！"
```

### 全局事件钩子（消息中间件）

Bot 提供全局前置 / 后置钩子，用于横切统计、审计、预处理，建议在插件 `on_load` 中注册：

```python
# 前置钩子：async (event, ctx) -> Optional[str]
# 返回非 None 时拦截该事件，返回值作为回复发送并终止分发
async def pre_hook(event, ctx):
    return None

# 后置钩子：async (event, ctx, reply) -> None
# 在消息回复发送后触发（reply 为最终回复或 None）
async def post_hook(event, ctx, reply):
    pass

self.bot.register_pre_hook(pre_hook)
self.bot.register_post_hook(post_hook)
```

钩子异常隔离（不影响主链路），注册自动去重。

### 插件加载方式

**方式一：Web UI 加载（推荐）**

在「插件管理」页面输入模块路径（如 `plugins.pingpong`），点击加载。

**方式二：内置插件**

将插件文件放入 `bot/plugin/builtin/` 目录，Bot 启动时自动加载。

**方式三：外部目录**

将插件放在项目根目录的 `plugins/` 文件夹中，确保 `plugins/__init__.py` 存在，然后用模块路径 `plugins.xxx` 加载。

### 常用 OneBot API

通过 `self.connection.call_api(action, params)` 调用：

| Action | 参数 | 说明 |
|--------|------|------|
| `send_msg` | `user_id` / `group_id`, `message` | 发送消息 |
| `send_group_msg` | `group_id`, `message` | 发送群消息 |
| `send_private_msg` | `user_id`, `message` | 发送私聊消息 |
| `delete_msg` | `message_id` | 撤回消息 |
| `get_group_member_list` | `group_id` | 获取群成员列表 |
| `get_group_member_info` | `group_id`, `user_id` | 获取群成员信息 |
| `set_group_kick` | `group_id`, `user_id` | 踢出群成员 |
| `set_group_ban` | `group_id`, `user_id`, `duration` | 禁言 |
| `group_poke` | `group_id`, `user_id` | 戳一戳 |

### 注意事项

- `on_load` 和 `on_unload` 是 `@abstractmethod`，**必须实现**（可以是 `pass`）
- 插件中不要使用阻塞操作（如 `time.sleep`），用 `asyncio.sleep` 代替
- `on_message` 返回空字符串 `""` 也会被当作回复发送，不需要回复时返回 `None`
- 插件可通过 `self.config` 修改配置，但需调用 `self.config.save()` 持久化
- 热重载会重新执行模块代码，类级别的可变状态会丢失
- 模块级装饰器注册的 Matcher 会自动关联到同模块的 PluginBase 子类
- 插件重载采用“先建后拆”：新版本加载成功前旧插件保持生效；新版本加载失败时旧插件继续工作，不会出现插件真空
- 重载后的模块若不再定义插件类，重载接口会返回失败（而非静默成功），旧插件保持生效

## API 接口

所有接口前缀 `/api`。启用鉴权（配置 `api_key`）时，除 `/api/bot/status`、`/api/bot/health`、`/api/auth/*` 外均需携带 `X-API-Key` 请求头；`api_key` 为空时全部免鉴权。

**错误响应与超时说明：**

- `/api/bot/start`、`/stop`、`/restart` 有超时保护（启动 30s / 停止 15s），超时返回 `504` 并自动尝试清理残留资源，可通过 `/api/bot/status` 确认实际状态
- `/api/plugin/load` 请求体必须为 JSON 且包含字符串字段 `module_path`，类型非法时返回 `422`
- 所有 `5xx` 错误的 `detail` 为通用文案（不暴露内部异常细节），详细原因见服务端日志

### Bot 控制 `/api/bot`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/status` | 否 | 获取 Bot 运行状态 |
| GET | `/health` | 否 | 健康检查 |
| POST | `/start` | 是 | 启动 Bot |
| POST | `/stop` | 是 | 停止 Bot |
| POST | `/restart` | 是 | 重启 Bot |

### 配置管理 `/api/config`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `` | 是 | 获取完整配置（敏感字段脱敏为 `***`） |
| PUT | `` | 是 | 更新配置（深度合并，`***` 占位符自动过滤） |
| GET | `/bot` | 是 | 获取 Bot 配置 |
| PUT | `/bot` | 是 | 更新 Bot 配置 |
| GET | `/llm` | 是 | 获取 LLM 配置 |
| PUT | `/llm` | 是 | 更新 LLM 配置（provider=custom 时强制校验 api_url） |
| GET | `/llm/presets` | 是 | 获取 LLM 提供商预设（api_url + 推荐 model，切换 provider 自动联动） |
| POST | `/llm/models` | 是 | 查询提供商可用模型列表（按 provider 调用对应 API，10s 超时，失败 400 透传原因） |
| GET | `/onebot` | 是 | 获取 OneBot 配置 |
| POST | `/llm/test` | 是 | 测试 LLM 连接（返回 `{available, message}`） |

### 插件管理 `/api/plugin`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `` | 是 | 获取插件列表 |
| GET | `/{name}` | 是 | 获取插件详情 |
| POST | `/{name}/reload` | 是 | 重载插件 |
| POST | `/load` | 是 | 加载外部插件（仅允许 `plugins.*` / `bot.plugin.builtin.*` 白名单前缀） |
| DELETE | `/{name}` | 是 | 卸载插件（内置插件 chat/admin/help/imagegen/knowledge 不可卸载） |

### 消息日志与用量 `/api/log`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/messages` | 是 | 搜索消息记录 |
| GET | `/messages/count` | 是 | 获取消息总数 |
| GET | `/messages/export` | 是 | 导出消息记录（CSV 流式，utf-8-sig，Excel 直接打开不乱码） |
| GET | `/usage` | 是 | LLM 用量统计（依赖 `log.usage_tracking`） |
| DELETE | `/messages` | 是 | 删除消息记录；支持 `user_id` / `group_id` / `before_days` 过滤，全部删除需显式 `confirm=true` |
| DELETE | `/sessions` | 是 | 清除所有会话（需 `confirm=true`） |
| GET | `/sessions` | 是 | 会话列表（按最后活跃排序，含条数与归属 QQ） |
| GET | `/sessions/messages` | 是 | 查看指定会话历史（`?key=private:10001` 或 `group:10001:20002`） |
| DELETE | `/sessions/one` | 是 | 删除指定会话（`?key=会话key`，带审计） |

### 群配置 `/api/group`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/list` | 是 | 群配置列表 |
| GET | `/{group_id}` | 是 | 获取单群配置 |
| PUT | `/{group_id}` | 是 | 更新群配置（Bot 开关等） |

### 登录与鉴权 `/api/auth`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/status` | 否 | 是否需要登录（配置 api_key 是否非空） |
| POST | `/login` | 否 | 登录；按来源 IP 防暴力限流，连续失败 5 次后冷却 60 秒返回 429 |

### 数据库备份 `/api/backup`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/db` | 是 | 在线备份到 `data/backups/`（sqlite backup API，文件名带随机后缀，保留最近 10 份） |

### 审计日志 `/api/audit`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/logs` | 是 | 审计日志倒序查询（配置变更 / 启停 / 登录 / 备份等） |

### WebSocket

| 路径 | 鉴权 | 说明 |
|------|------|------|
| `/api/ws/log` | `token` 查询参数 | 实时推送消息记录，连接后自动接收新消息；60s 心跳保活（90s 无消息断开），连接数上限 32 |
| `/api/ws/chat` | `token` 查询参数 | 对话调试台：客户端发送 `{"message": "...", "user_id": 900000001}`，服务端逐块返回 `{"type":"delta","text":...}`，结束返回 `{"type":"done"}`；流式调用 LLM，独立连接池（上限 32） |

## 打包为 exe

使用 PyInstaller 将 Qingci-Bot 打包为 Windows 可执行程序（onedir 模式）。

### 构建

```powershell
# 依赖：PyInstaller 已安装在 .venv（uv pip install pyinstaller）
# Web UI 需先构建（web\dist 存在时可跳过）
cd web; npm install; npm run build; cd ..

# 一键打包
.\build.ps1
```

> `config.yaml` 已被 `.gitignore` 忽略（避免密钥入库）。新克隆的仓库中没有该文件，打包前需先从 `config.example.yaml` 复制一份并填入配置：
>
> ```powershell
> Copy-Item config.example.yaml config.yaml
> ```

产物位于 `dist\qingci-bot\`：

```
dist\qingci-bot\
├── qingci-bot.exe        # 主程序（带控制台，日志直接可见）
├── _internal\            # Python 运行时与依赖（勿动）
├── web\dist\             # Web UI 静态资源（build.ps1 复制）
├── config.yaml           # 配置文件（build.ps1 从项目根复制/暂存还原）
└── data\                 # SQLite 数据库 / 备份 / 敏感词库
```

### 运行

```powershell
.\dist\qingci-bot\qingci-bot.exe              # Bot + API 服务
.\dist\qingci-bot\qingci-bot.exe --no-bot     # 仅 API / Web UI
.\dist\qingci-bot\qingci-bot.exe --port 9000  # 指定端口
```

启动后访问 `http://127.0.0.1:8080/ui/`。

> **启动性能**：litellm 采用延迟导入，启动阶段不会加载该重型依赖（节省约 3.5 秒），仅首次真正调用 LLM 时一次性导入；首次运行 `config.yaml` 缺失时自动生成默认配置，无需手工准备。

### 注意事项

- `config.yaml` 与 `data\` 按 **exe 所在目录** 相对定位：分发时整个 `dist\qingci-bot\` 目录一起拷贝，勿单独移动 exe。
- 首次运行若缺少数据库会自动建表（SQLModel create_all）；`config.yaml` 缺失时会自动生成默认配置。
- 重新执行 `build.ps1` 不会覆盖产物目录中已有的 `config.yaml` 与 `data\`（脚本会先暂存后还原）。
- `--desktop` 桌面模式依赖系统 WebView2 运行时（pywebview EdgeChromium 后端），未安装的系统可能无法打开窗口。
- 如需无控制台窗口模式，将 `qingci-bot.spec` 中 `console=True` 改为 `False` 后重新构建。

## 许可证

MIT
