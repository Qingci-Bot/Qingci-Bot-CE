# Qingci-Bot CE

> 本项目底层核心代码由 [**Zhou Zhe (aka luoqingci)**](https://atomgit.com/luoqingci) 原创，并授予 [Qingci-Bot](https://atomgit.com/Qingci-Bot) 组织持续开发。

基于 Python 的 QQ 机器人框架，对接 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot)（OneBot 11 协议），支持 LLM 智能对话、Web UI 和桌面应用。

> 独立插件开发：[Plugins-SDK](https://atomgit.com/Qingci-Bot/Plugins-SDK) — 零依赖插件开发 SDK，无需克隆主项目即可开发插件
>
> 插件协议层（`PluginBase`/`Matcher`/`Permission`/`Rule`/`MessageContext`）统一由 Plugins-SDK 维护，主项目 `bot/plugin/` 下为薄转发，内置插件与外部插件共用同一套 API
>
> 系统架构、项目结构、技术栈详见 [ARCHITECTURE.md](./ARCHITECTURE.md)

## 特性

- **OneBot 11 反向 WebSocket**：基于 [aiocqhttp](https://github.com/nonebot/aiocqhttp)，完整支持 OneBot v11 协议（消息段解析、API 调用、事件总线）
- **LLM 统一接口**：基于 [litellm](https://github.com/BerriAI/litellm)，支持 7 大提供商（OpenAI / DeepSeek / Ollama / SiliconFlow / Claude / Gemini / 自定义），含流式响应、Function Calling、多模态；填好 API Key 后可一键拉取提供商可用模型列表
- **人格/人设系统**：可配置多组人格（system_prompt 集合），聊天中 `/persona` 命令随时切换（会话级覆盖），Web UI 可视化管理
- **会话上下文管理**：按群聊/用户独立维护对话历史，内存 + 数据库双写持久化，按条数与 Token 双重裁剪（可选摘要压缩）；Web UI 按会话分组可视化查看 / 删除
- **插件系统**：借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持命令/前缀/关键词/正则/通知/请求匹配，优先级调度、权限控制、插件间依赖声明（require + PEP 440 版本约束）、插件级配置（config.yaml 节）、插件间导出/导入（export/require）、插件级中间件（before/after handler）、handler 参数级依赖注入（Depends）、全局生命周期钩子（on_startup/on_shutdown/on_bot_connect/on_metaevent）、跨插件事件总线（EventBus 发布-订阅）、插件级 LLM 工具声明（`@llm_tool` 参与 Function Calling）、指令系统增强（别名 / 子指令 / 类型化参数）、插件数据目录（data_dir）、国际化（i18n）、在线插件安装与依赖自动安装、配置 schema 自动生成 Web 配置表单、开发期自动热重载、细粒度事件处理钩子（run_preprocessor Matcher 运行前钩子 + on_calling_api 平台接口调用钩子）、插件状态管理（PluginStatus 枚举）、执行指标监控、元数据发现（plugin.json）；支持加载/卸载/重载/禁用/启用，禁用时保留实例并跳过事件分发。协议层（PluginBase/Matcher/Rule/Permission/MessageContext）由独立插件 SDK 单一维护，内置插件与外部插件行为一致
- **安全与运维**：API Key 鉴权（登录防暴力限流）、敏感词过滤、对话限流、登录审计、数据库在线备份、错误告警、结构化 JSON 日志（可选）
- **增强能力**：AI 图片生成、轻量知识库（关键词检索零依赖；向量检索需可选依赖 lancedb）、会话摘要（历史裁剪）、Function Calling、MCP 服务器接入、定时任务调度器、LLM 用量统计
- **数据库 ORM**：SQLModel 模型定义 + Alembic 迁移管理，异步会话（aiosqlite + WAL 模式），支持在线备份与消息 CSV 导出
- **Web UI**：原神风格暗色主题，登录页 / 仪表盘（用量图表）/ LLM 配置（提供商联动 + 模型列表 + 人格 + MCP 管理）/ 对话调试台（流式聊天测试）/ 群配置 / 插件管理（分类筛选 + 状态管理 + 指标面板）/ 命令管理（冲突标记 + 禁用/优先级调整 + 权限等级显示）/ 消息日志（消息流 + 会话记录）/ 登录审计 / 系统设置。侧边栏内置实例列表，可新建/删除/切换/重命名实例
- **桌面应用**：PyWebView 套壳 + 系统托盘（关闭窗口自动驻留后台），开机自启；启动时显示即时加载画面，重型模块延迟导入，双击 exe 后无感知等待
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

# 安装核心依赖（运行项目所需）
uv pip install -e . --python .venv\Scripts\python.exe

# 安装开发依赖（含测试、构建、代码质量工具）
uv pip install -e ".[dev]" --python .venv\Scripts\python.exe
```

> 依赖分组说明：
>
> | 分组 | 安装命令 | 内容 |
> |------|----------|------|
> | 核心 | `uv pip install -e .` | 运行时依赖（FastAPI、litellm、OneBot、qingci-plugin-sdk 等） |
> | `[vector]` | `uv pip install -e ".[vector]"` | 向量知识库（lancedb，可选；缺失时 RAG 自动回退关键词检索） |
> | `[test]` | `uv pip install -e ".[test]"` | pytest / pytest-asyncio / pytest-cov / httpx |
> | `[build]` | `uv pip install -e ".[build]"` | pyinstaller（`.\build.ps1` 依赖） |
> | `[dev]` | `uv pip install -e ".[dev]"` | 以上全部 + ruff / mypy（代码质量工具） |
>
> 插件协议层 SDK（`qingci-plugin-sdk`）作为 git 依赖随核心依赖安装；本地开发时若需对 SDK 改代码，可优先 `uv pip install -e ..\Plugins-SDK`（与 `build.ps1` 一致），覆盖 git 依赖版本。
>
> 若跳过 `pyproject.toml`，可手动安装核心依赖（另需 `pip install git+https://atomgit.com/Qingci-Bot/Plugins-SDK.git` 安装 SDK）：
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

# 以指定实例启动（instances/<name>/ 自包含目录：config.yaml + data/ + plugins/）
.venv\Scripts\python main.py --instance <name>
```

启动必须绑定一个实例（无全局模式）：未指定 `--instance` 时自动选择默认实例（`default` 优先，其次名称排序第一个）；若实例数为 0 则自动创建 `default` 实例。每个实例是 `instances/<name>/` 下的自包含目录，侧边栏「实例」区块可新建/删除/切换实例；切换会以目标实例重启进程。

启动后访问 `http://127.0.0.1:8080/ui` 进入管理界面。

> **Web UI 未构建时**：若 `web/dist` 缺失或不完整，访问 `/` 会返回构建提示页（引导在 `web/` 目录执行 `npm install` 与 `npm run build`），API 服务本身仍正常可用。克隆仓库后首次启动前请先构建前端。

## 3. 运行测试

```bash
# 运行全部测试（含覆盖率报告）
pytest

# 仅运行指定模块
pytest tests/test_api.py
pytest tests/test_config.py
pytest tests/test_db.py
```

> 测试框架：pytest + pytest-asyncio + pytest-cov。覆盖率目标为 `bot` 和 `api` 模块，报告通过 `--cov-report=term-missing` 输出未覆盖行。

## 4. 代码质量

```bash
# 代码风格检查
ruff check .

# 自动修复
ruff check --fix .

# 格式化检查
ruff format --check .

# 自动格式化
ruff format .

# 类型检查
mypy bot api
```

> 推荐配置 [pre-commit](https://pre-commit.com/) hooks 在提交前自动检查：
>
> ```bash
> pre-commit install
> ```
>
> 配置文件 `.pre-commit-config.yaml` 已包含 ruff 格式检查和通用文件检查（YAML/TOML/JSON 语法、行尾空格、大文件等）。

## 5. 配置 LLBot

在 LLBot 中添加反向 WebSocket 连接：

- 地址：`ws://127.0.0.1:3001/ws`（端口默认 3001，需与 `config.yaml` 的 `onebot.port` 保持一致）
- Access Token：留空或与 `config.yaml` 中 `onebot.access_token` 保持一致

LLBot 会自动携带 OneBot v11 标准的 `X-Client-Role: universal` 和 `X-Self-ID` header 连接。

## 6. 配置 LLM

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
| `--config` | 配置文件路径 | 实例内 `config.yaml` |
| `--instance` | 启动到指定实例（`instances/<name>/` 自包含目录） | 默认实例 |
| `--data-dir` | 指定可写数据根目录（DB/日志/插件数据等），用于多实例隔离；未指定时默认落在实例内 `data/` | `instances/<name>/data` |

## 管理命令

管理员分为两级：**超级管理员**（唯一，`bot.super_admin`）与**普通管理员**（多个，`bot.admin_users`，超级管理员自动继承普通管理员权限）。

**普通管理员命令**（`bot.admin_users` 中的 QQ 号）：

| 命令 | 说明 |
|------|------|
| `/status` | 查看 Bot 运行状态（OneBot 连接 / LLM 可用性 / 消息记录数） |
| `/clear` | 清除当前会话历史 |

**超级管理员命令**（仅 `bot.super_admin` 的 QQ 号）：

| 命令 | 说明 |
|------|------|
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
  name: Qingci-Bot CE
  super_admin: 123456789          # 超级管理员 QQ 号（唯一）
  admin_users: [123456789]        # 普通管理员 QQ 号列表
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
  system_prompt: 你是一个友好的 QQ 机器人助手。请用简洁、自然的中文回复。
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
hot_reload:
  enabled: false                   # 插件开发期自动热重载（默认关闭，生产环境建议关闭）
  interval: 2.0                    # 轮询插件目录文件变更的间隔（秒）
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
  enabled: false                   # 轻量知识库（默认关闭）
  mode: keyword                    # 检索模式: keyword（关键词检索）/ vector（LanceDB 向量检索）
  embedding_model: ''              # 向量模型（vector 模式使用，如 text-embedding-3-small）
  embedding_api_url: ''            # 向量 API 地址（留空复用 llm.api_url）
  embedding_api_key: ''            # 向量 API Key（留空复用 llm.api_key）
  top_k: 3                         # 检索返回的最相关分块数
  knowledge_dir: data/knowledge    # 知识库目录（相对项目根目录）
  chunk_size: 400                  # 文档分块大小（字符数）
  chunk_overlap: 50                # 相邻分块重叠字符数
  max_inject_chars: 800            # 注入 system_prompt 的参考资料长度上限
  collection_name: qingci_knowledge # LanceDB 集合名（vector 模式使用）
session_summary:
  enabled: false                   # 会话摘要（默认关闭；与 llm.enable_summary 等价）
  keep_recent_turns: 3             # 摘要时保留最近 N 轮原文
  max_messages: 20                 # 触发摘要的条数阈值
  max_tokens: 4096                 # 触发摘要的 token 阈值
  summary_max_tokens: 512          # 摘要生成单次回复最大 token
log:
  usage_tracking: true             # LLM 用量入库（可退出的遥测；Dashboard 用量统计依赖该数据）
  level: INFO                      # 日志级别：DEBUG / INFO / WARNING / ERROR
  log_file_enabled: false          # 文件日志开关（默认关闭，仅控制台输出）
  log_file_max_bytes: 10485760     # 单文件最大字节数（默认 10 MB）
  log_file_backup_count: 5         # 保留备份数
  log_dir: logs                    # 日志目录（相对项目根目录）
api_key: ''                        # API 鉴权密钥
```

## 进阶功能说明（功能开关均默认关闭）

| 配置节 | 功能 | 默认值 | 说明 |
|--------|------|--------|------|
| `rate_limit` | 对话限流 | `enabled: false` | 每用户每日对话上限 + 两次对话冷却间隔，超限回复提示 |
| `filter` | 敏感词过滤 | `enabled: false` | 词库为 `data/sensitive_words.txt`（一行一词，支持 `#` 注释）；词库为空时 `/filter` 命令与日志会明确提示；管理员可通过 `exempt_admins` 豁免 |
| `scheduler` | 定时任务调度器 | `enabled: true` | 调度器基座，由插件注册任务；无任务注册时零副作用 |
| `hot_reload` | 插件自动热重载 | `enabled: false` | 开发期监听 `plugins/` 目录 `.py` 文件变更并自动重载对应插件；`interval` 为轮询间隔（秒）；生产环境建议关闭 |
| `alert` | 错误告警 | `enabled: false` | 冷却窗口内 ERROR 日志达到 `error_threshold` 条时向管理员发消息告警，带 `cooldown_minutes` 冷却 |
| `image` | 图片生成 | `enabled: false` | `/image <提示词>`（或 `/画图`）命令；`image.api_key` 为空时回退 `llm.api_key`；成功后以 CQ 图片段回复 |
| `rag` | 轻量知识库 | `enabled: false` | 双模式：`keyword`（纯 Python 关键词检索，无重型依赖）/ `vector`（LanceDB 向量检索 + litellm embedding，语义更精准；需可选依赖 `lancedb`，未安装时自动回退 keyword 并告警）；开启后对话自动注入检索到的参考资料；`/kb` 命令管理文档（add/list/search/remove/reload）。vector 模式的初始化步骤见 [ARCHITECTURE.md](./ARCHITECTURE.md#向量检索rag初始化) |
| `session_summary` | 会话摘要 | `enabled: false` | 与 `llm.enable_summary` 等价，任一为 true 即启用；上下文超过条数/token 阈值时将较早消息摘要压缩，保留最近 N 轮原文 |
| `log.usage_tracking` | LLM 用量入库 | `true` | 可退出的遥测：关闭后 chat/摘要/图片不再写 usage_logs，Dashboard 用量统计将为空 |
| `llm.enable_tools` | Function Calling | `false` | 启用工具调用（内置 `get_current_time` / `random_quote`，可经 ToolRegistry 扩展）；`max_tool_rounds` 限制最大轮次（默认 5） |
| `llm.personas` | 人格/人设 | `[]` | 多组 system_prompt；聊天中 `/persona` 切换（会话级覆盖）、`/persona 列表` 查看；Web UI「LLM 配置」管理 |
| `llm.mcp_servers` | MCP 服务器 | `[]` | 连接外部 MCP 服务器（stdio/HTTP 传输），工具注册为 `mcp_{服务器名}_{工具名}` 供 LLM 调用；需开启 `enable_tools`，修改后重启 Bot 生效 |
| `llm.provider` | 提供商联动 | `openai` | 切换 provider 自动带出预设 api_url/model（openai/deepseek/ollama/siliconflow/claude/gemini/custom 共 7 个）；`api_url` 非空统一走 OpenAI 兼容协议 |
| `llm.timeout` / `llm.num_retries` | 请求超时与重试 | `60` / `2` | 单次 LLM 请求超时秒数与失败重试次数 |
| `bot.log_json` | 结构化 JSON 日志 | `false` | 面向机器可读的日志采集场景 |
| `log.log_file_enabled` | 文件日志轮转 | `false` | 启用后日志写入 `log_dir/qingci-bot.log`，按 `log_file_max_bytes` 大小轮转，保留 `log_file_backup_count` 个备份 |

---

> 插件开发、API 接口、前端开发、打包详见 [PLUGIN_DEV.md](./PLUGIN_DEV.md)
>
> 独立插件开发 SDK：[Plugins-SDK](https://atomgit.com/Qingci-Bot/Plugins-SDK) — 零依赖插件开发工具包，无需克隆主项目即可开发插件

## 文档

- [CHANGELOG.md](./CHANGELOG.md) — 版本变更记录
- [CONTRIBUTING.md](./CONTRIBUTING.md) — 贡献指南
- [SECURITY.md](./SECURITY.md) — 安全策略与漏洞报告
- [ARCHITECTURE.md](./ARCHITECTURE.md) — 系统架构与技术栈
- [PLUGIN_DEV.md](./PLUGIN_DEV.md) — 插件开发指南
- [docs/PROJECT_STRUCTURE.md](./docs/PROJECT_STRUCTURE.md) — 项目结构规范（目录职责与产物归属）
- [docs/CODING_STANDARDS.md](./docs/CODING_STANDARDS.md) — 编码规范（类型 / 命名 / 分层 / Git 约定）

## 许可证

[GNU General Public License v3.0 (GPLv3)](./LICENSE)，衍生作品必须同样以 GPLv3 开源。