# Qingci-Bot CE

> **代码托管**：本项目以 [GitHub](https://github.com/Qingci-Bot/Qingci-Bot-CE) 为唯一仓库；贡献与提 PR 一律以 GitHub 为准。

> 本项目底层核心代码由 [**Zhou Zhe (aka luoqingci)**](https://github.com/luoqingciya) 原创，并授予 [Qingci-Bot](https://github.com/Qingci-Bot) 组织持续开发。

基于 Python 的多平台机器人框架，内部统一采用 **OneBot 12 事件模型**（`type` / `detail_type` / `message[]` 消息段），基于 [aiocqhttp](https://github.com/nonebot/aiocqhttp) 对接任意 OneBot 11 反向 WebSocket 协议端（如 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot) / NapCat / go-cqhttp，作为兼容输入层自动翻译为 v12 事件），支持 LLM 智能对话、Web UI 和桌面应用。适配器将各平台归一化为 OneBot 12 内部模型，插件对来源平台完全无感知。

> 独立插件开发：[Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK) — 零依赖插件开发 SDK，无需克隆主项目即可开发插件
>
> 插件协议层（`PluginBase`/`Matcher`/`Permission`/`Rule`/`MessageContext`）统一由 Plugins-SDK 维护，主项目 `bot/plugin/` 下为薄转发，内置插件与外部插件共用同一套 API
>
> 系统架构、项目结构、技术栈详见 [ARCHITECTURE.md](./ARCHITECTURE.md)

## 特性

- **OneBot 12 内核**：内部统一采用 OneBot 12 事件模型（`type` / `detail_type` / 标准 `{type,data}` 消息段，媒体以 `file_id` 引用）；基于 [aiocqhttp](https://github.com/nonebot/aiocqhttp) 对接任意 OneBot 11 反向 WebSocket 协议端（如 LLBot / NapCat / go-cqhttp），v11 事件在入口由 `v11_compat` 翻译层自动归一化为 v12 事件，并对存量插件保留 v11 兼容字段（`post_type` / `message_type` / `raw_message`）——OneBot 11 只是"众多平台之一"
- **多平台适配器**：平台协议归一化为 OneBot 12 内部事件模型（`PlatformAdapter` 契约），插件对平台无感知；内置 OneBot 11（v11 反向 WS + `v11_compat` 翻译）+ **OneBot 12（原生反向 WS，事件直通无需翻译，动作 JSON-RPC；扩展通知如红包运气王/荣誉变更/名片变更/精华消息/群签到/好友戳一戳已类型化，插件可 `on_notice` + 类型注解消费）** + Telegram（Bot API 长轮询，`platforms.telegram` 配置启用），回复按事件来源平台自动路由；Telegram 适配器支持群聊 `@Bot` 提及触发（at 触发模式）、图片/语音/视频收发（收到 photo → `image`、voice → `voice`、video → `video` v12 段；发送将 v12 `image`/`voice`/`video` 段映射到 `sendPhoto`/`sendVoice`/`sendVideo`）、回复段与成员变动通知（成员进出群/权限变更归一化为 `group_member_increase`/`group_member_decrease`/`group_admin_*` notice）；长轮询采用有限并发消费（慢更新不阻塞同批）且失败更新自动确认跳过避免重放，连接失败指数退避并自动重连，Bot Token 支持运行时热更新（自动重验身份），HTTP 超时/重试可配置，API 调用错误按 401/403/404 分类，平台状态接口暴露连接健康指标（连续错误数/最近错误与断连时间/退避状态）
- **LLM 统一接口**：基于 [litellm](https://github.com/BerriAI/litellm)，支持 7 大提供商（OpenAI / DeepSeek / Ollama / SiliconFlow / Claude / Gemini / 自定义），含流式响应、Function Calling、多模态；填好 API Key 后可一键拉取提供商可用模型列表
- **人格/人设系统**：可配置多组人格（system_prompt 集合），聊天中 `/persona` 命令随时切换（会话级覆盖），Web UI 可视化管理
- **会话上下文管理**：按群聊/用户独立维护对话历史，内存 + 数据库双写持久化，按条数与 Token 双重裁剪（可选摘要压缩）；Web UI 按会话分组可视化查看 / 删除
- **插件系统**：借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持命令/前缀/关键词/正则/通知/请求匹配，优先级调度、权限控制、插件间依赖声明（require + PEP 440 版本约束）、插件级配置（config.yaml 节）、插件间导出/导入（export/require）、插件级中间件（before/after handler）、handler 参数级依赖注入（Depends）、全局生命周期钩子（on_startup/on_shutdown/on_bot_connect/on_metaevent）、跨插件事件总线（EventBus 发布-订阅）、插件级 LLM 工具声明（`@llm_tool` 参与 Function Calling）、指令系统增强（别名 / 子指令 / 类型化参数）、插件数据目录（data_dir）、国际化（i18n）、在线插件安装与依赖自动安装、配置 schema 自动生成 Web 配置表单、开发期自动热重载、细粒度事件处理钩子（run_preprocessor Matcher 运行前钩子 + on_calling_api 平台接口调用钩子）、插件状态管理（PluginStatus 枚举）、执行指标监控、元数据发现（plugin.json）；支持加载/卸载/重载/禁用/启用，禁用时保留实例并跳过事件分发；卸载默认仅删代码目录、保留数据与依赖（便于重装），插件管理页可「彻底删除（purge）」连数据目录与第三方依赖一并清除。协议层（PluginBase/Matcher/Rule/Permission/MessageContext）由独立插件 SDK 单一维护，内置插件与外部插件行为一致
- **安全与运维**：API Key 鉴权（登录防暴力限流）、敏感词过滤、对话限流、登录审计、数据库在线备份、错误告警、结构化 JSON 日志（可选）
- **增强能力**：AI 图片生成、轻量知识库（关键词检索零依赖；向量检索需可选依赖 lancedb）、会话摘要（历史裁剪）、Function Calling（内置时间/一言/群事件查询工具）、MCP 服务器接入、定时任务调度器、LLM 用量统计
- **HTML 渲染服务**：基于 Playwright 无头 Chromium 将 HTML 渲染为 JPEG/PNG（可选依赖 `[render]`），供签到卡等「HTML 模板 → 图片消息」插件复用；playwright 未安装/浏览器缺失时自动降级不可用，不影响框架启动
- **数据库 ORM**：SQLModel 模型定义 + Alembic 迁移管理，异步会话（aiosqlite + WAL 模式），支持在线备份与消息 CSV 导出
- **Web UI**：原神风格暗色主题，登录页 / 仪表盘（用量图表）/ LLM 配置（提供商联动 + 模型列表 + 人格 + MCP 管理）/ 对话调试台（流式聊天测试）/ 群配置 / 插件管理（分类筛选 + 状态管理 + 指标面板 + 卸载/彻底删除 + 插件市场一键安装/更新/搜索）/ 命令管理（冲突标记 + 禁用/优先级调整 + 权限等级显示）/ 消息日志（消息流 + 会话记录）/ 运行日志（实时日志流 + 级别过滤，受 `log.run_log_enabled` 开关控制）/ 登录审计 / 系统设置。独立「实例管理」页面支持新建/删除/切换/重命名实例（含端口、启用的适配器、数据占用等信息）
- **桌面应用**：PyWebView 套壳 + 系统托盘（关闭窗口自动驻留后台）；启动时显示即时加载画面，重型模块延迟导入，双击 exe 后无感知等待
- **离线可用**：前端资源本地打包，无外部 CDN 依赖；litellm 延迟导入，启动不加载重型依赖

---

# 使用指南

## 环境要求

- Python 3.10+（推荐 3.12）
- 任意 OneBot 11 协议端（如 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot) / NapCat / go-cqhttp）
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
> | `[render]` | `uv pip install -e ".[render]"` | HTML → 图片渲染（playwright，可选；安装与 Chromium 下载见下方「启用 HTML 渲染」；缺失时渲染能力自动降级不可用，调用方回退） |
> | `[test]` | `uv pip install -e ".[test]"` | pytest / pytest-asyncio / pytest-cov / httpx |
> | `[build]` | `uv pip install -e ".[build]"` | pyinstaller + playwright（`.\build.ps1` 依赖；playwright 用于打包时内置无头浏览器） |
> | `[dev]` | `uv pip install -e ".[dev]"` | 以上全部 + ruff / mypy（代码质量工具） |
>
> **启用 HTML 渲染**：源码运行时安装 `[render]` 分组后还需下载 Chromium（国内网络建议走 npm 镜像，否则容易卡住/超时）：
>
> ```bash
> $env:PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright"
> uv run playwright install chromium
> ```
>
> **打包版（EXE）已全内置**：`build.ps1` 构建时自动下载无头 Chromium 到产物目录 `ms-playwright\`，运行时经 `PLAYWRIGHT_BROWSERS_PATH` 定位，EXE 开箱即可渲染签到卡，无需最终用户再执行安装。构建期同样可用上述 `PLAYWRIGHT_DOWNLOAD_HOST` 走国内镜像。
>
> 浏览器缺失或下载失败时渲染能力自动降级不可用（`/api/bot/status` 的 `render` 字段可查状态），不影响框架启动。
> 插件协议层 SDK（`qingci-plugin-sdk`）作为 git 依赖（默认指 [Gitee 镜像](https://gitee.com/qingci-bot/Plugins-SDK)，国内拉取更快）随核心依赖安装；本地开发时若需对 SDK 改代码，可优先 `uv pip install -e ..\Plugins-SDK`（与 `build.ps1` 一致），覆盖 git 依赖版本。
>
> 若跳过 `pyproject.toml`，可手动安装核心依赖（另需 `pip install git+https://gitee.com/qingci-bot/Plugins-SDK.git` 安装 SDK）：
>
> ```bash
> uv pip install fastapi "uvicorn[standard]" websockets aiocqhttp aiohttp aiosqlite \
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

启动必须绑定一个实例（无全局模式）：未指定 `--instance` 时自动选择默认实例（`default` 优先，其次名称排序第一个）；若实例数为 0 则自动创建 `default` 实例。每个实例是 `instances/<name>/` 下的自包含目录，独立「实例管理」页（`/instances`）支持新建/删除/切换/重命名实例（含端口、启用的适配器、数据占用等信息）；切换会以目标实例重启进程。**创建实例时可绑定主平台**（OneBot / OneBot 12 / Telegram）：创建后系统设置即针对该平台语义落位——OneBot 主平台实例启动反向 WS 服务端（`onebot.enabled`），Telegram 主平台实例自动关闭反向 WS 并启用 Telegram 适配器，`super_admin` / 管理员 / 黑白名单均以平台无关字符串 ID 配置。

启动后访问 `http://127.0.0.1:8080/ui` 进入管理界面。

> **Web UI 未构建时**：若 `web/dist` 缺失或不完整，访问 `/` 会返回构建提示页（引导在 `web/` 目录执行 `npm install` 与 `npm run build`），API 服务本身仍正常可用。克隆仓库后首次启动前请先构建前端。

### 2.1 Docker 容器部署（推荐，Linux 最省心）

容器内以 **Headless 方式**运行后端（Bot + WebUI/API），不启用桌面 GUI 与启动画面。需要 [Docker](https://docs.docker.com/engine/install/) 与 Compose 插件：

```bash
# 在项目根目录
docker compose up -d        # 构建 + 后台启动
docker compose logs -f      # 查看日志
docker compose restart      # 重启（改配置后生效）
docker compose down         # 停止
```

- **端口**：`8080`（WebUI/API）、`3001`（OneBot 反向 WS）
- **数据持久化**：`./instances:/app/instances` 卷挂载实例目录（config.yaml / data / 插件），不随镜像重建丢失
- **首次启动**：自动创建 `instances/default/config.yaml`；设置 `llm.api_key` 后即可对话
- **外部 OneBot 前端连入**：将该实例 `onebot.host` 改为 `0.0.0.0` 后 `docker compose restart`
- 文件内容与完整说明见 `Dockerfile` / `docker-compose.yml`（`.dockerignore` 排除 venv/产物，实例目录不进镜像）

> 构建依赖 `qingci-plugin-sdk`（Gitee git 依赖，见 [pyproject.toml](./pyproject.toml)）需构建期联网；若改用了私有 SDK 克隆地址，请在构建前配置好凭据。

### 2.2 Linux 源码部署（一键脚本 install.sh）

核心运行（Bot + WebUI/API）为纯 Python，无需任何 GUI 系统依赖，Linux/macOS 均可直接跑：

```bash
chmod +x install.sh
./install.sh                        # 核心依赖（自动检测 Python>=3.10，优先 uv，否则 pip）
./install.sh --vector               # 追加向量知识库（lancedb）
./install.sh --with-gui             # 追加桌面 GUI 系统库（仅需桌面模式时；可选）
./install.sh --dev                  # 追加测试/构建/质量工具
```

脚本会：自动安装系统依赖（git / 编译工具；需 root，可用 `SKIP_SYS_DEPS=1` 跳过）→ 创建 `.venv` → 安装核心依赖。装完启动：

```bash
.venv/bin/python main.py --instance default
```

- WebUI：`http://127.0.0.1:8080/ui`
- 编辑实例配置：`instances/default/config.yaml`
- 外部 OneBot 前端连入前，把该实例 `onebot.host` 改为 `0.0.0.0`
- **桌面 GUI（`--desktop`）**：Linux 下需要 GTK 系系统库（`libwebkit2gtk` / `libgtk-3` / `libappindicator`，Debian 系可看 `install.sh --with-gui` 列出的包名）；启动画面（splash）为 Windows 专属，Linux 自动跳过。**建议 Linux 优先使用 Docker 或 Headless + WebUI 模式**
- 单实例保护基于 Windows 命名互斥量，Linux 下自动降级（允许多开，不阻塞启动）

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

## 5. 配置 OneBot 协议端

### 5.1 OneBot 11（默认）

在 OneBot 11 协议端（如 LLBot / NapCat / go-cqhttp）中添加反向 WebSocket 连接：

- 地址：`ws://127.0.0.1:3001/ws`（端口默认 3001，需与 `config.yaml` 的 `onebot.port` 保持一致）
- Access Token：留空或与 `config.yaml` 中 `onebot.access_token` 保持一致

协议端会自动携带 OneBot v11 标准的 `X-Client-Role: universal` 和 `X-Self-ID` header 连接。接入的 v11 事件会由 `bot/core/v11_compat.py` 翻译层自动归一化为 OneBot 12 事件（`type`/`detail_type`）后进入核心调度，插件侧仍能读取兼容字段（`post_type`/`message_type`/`raw_message`）。

### 5.2 OneBot 12（原生，无需翻译）

创建实例时选择 `OneBot 12` 主平台（或手动设置 `platforms.onebot12.enabled: true`），在支持 OneBot 12 的协议端（如 NapCat / Lagrange.OneBot）中添加反向 WebSocket 连接：

- 地址：`ws://127.0.0.1:3002/`（端口默认 3002，需与 `config.yaml` 的 `platforms.onebot12.port` 保持一致）
- Access Token：留空或与 `config.yaml` 中 `platforms.onebot12.access_token` 保持一致（实现端以 `Authorization: Bearer` 或 `?access_token=` 携带）

事件以 OneBot 12 标准格式（`type`/`detail_type`/`message[]`）直接进入核心调度，动作以 JSON-RPC（`send_message` 等）调用——与 v11 相比省去翻译层，回复段原生 v12 表达。

## 6. 配置 LLM

在 Web UI 的「LLM 配置」页面填写 API 信息，或直接编辑 `config.yaml`：

```yaml
llm:
  provider: deepseek              # openai / deepseek / ollama / siliconflow / claude / gemini / custom
  api_url: https://api.deepseek.com/v1  # 留空则按 provider 直连官方
  api_key: sk-your-key
  model: deepseek-chat
  system_prompt: 你是一个友好、乐于助人的机器人助手。
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
| `--port` | API 端口 | 实例元数据端口（首个实例 8080，后续递增） |
| `--host` | API 监听地址 | 127.0.0.1 |
| `--config` | 配置文件路径 | 实例内 `config.yaml` |
| `--instance` | 启动到指定实例（`instances/<name>/` 自包含目录） | 默认实例 |
| `--data-dir` | 指定可写数据根目录（DB/日志/插件数据等），用于多实例隔离；未指定时默认落在实例内 `data/` | `instances/<name>/data` |

## 管理命令

管理员分为两级：**超级管理员**（唯一，`bot.super_admin`）与**普通管理员**（多个，`bot.admin_users`，超级管理员自动继承普通管理员权限）。

**普通管理员命令**（`bot.admin_users` 中的平台无关用户 ID，如 QQ 号 / Telegram 用户 ID）：

| 命令 | 说明 |
|------|------|
| `/status` | 查看 Bot 运行状态（OneBot 连接 / LLM 可用性 / 消息记录数） |
| `/clear` | 清除当前会话历史 |

**超级管理员命令**（仅 `bot.super_admin` 对应的平台无关用户 ID）：

| 命令 | 说明 |
|------|------|
| `/blacklist add <用户ID>` | 添加用户到黑名单（平台无关用户 ID） |
| `/blacklist remove <用户ID>` | 从黑名单移除用户 |
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
  - `GET /api/config/wizard/status`、`POST /api/config/wizard`、`POST /api/config/wizard/skip`（首次启动向导）
- 在 Web UI 的「系统设置」页面可同时配置服务端 Key 和浏览器端 Key
- WebSocket（`/api/ws/log`、`/api/ws/chat`、`/api/ws/runlog`）通过 `token` 查询参数或 `sec-websocket-protocol: api-key.<key>` 子协议鉴权，方式同上

## 配置文件说明

> 配置模板见 `config.example.yaml`（敏感字段已脱敏，复制为 `config.yaml` 后按需修改）。
> 下方为完整字段说明：

```yaml
bot:
  name: Qingci-Bot CE
  super_admin: '123456789'         # 超级管理员 ID（唯一；平台无关字符串标识，如 QQ 号 / Telegram 用户 ID）
  admin_users: ['123456789']       # 普通管理员 ID 列表
  trigger_mode: at                 # 触发方式: at / keyword / always
  trigger_keywords: ["/bot", "/ai"] # keyword 模式的触发词
  group_blacklist: []              # 群黑名单（平台无关字符串标识）
  user_blacklist: []               # 用户黑名单
  log_json: false                  # 结构化 JSON 日志（false 使用普通文本日志）
  auto_install_plugin_deps: true   # 自动安装插件声明的第三方依赖（关闭以降低供给链风险）
onebot:
  enabled: true                    # 是否启动 OneBot 反向 WS 服务端（Telegram/OneBot 12 主平台实例可设为 false）
  host: 127.0.0.1
  port: 3001                       # 协议端连接 ws://host:port/ws
  access_token: ''
platforms:
  onebot12:                        # OneBot 12 原生反向 WS 适配器（默认关闭）
    enabled: false                 # true 启用后协议端（NapCat/Lagrange 等）连 ws://host:port/
    host: 127.0.0.1
    port: 3002                     # 与 onebot.port 区分
    access_token: ''
  telegram:                        # Telegram 平台适配器（默认关闭）
    enabled: false
    token: ''
    poll_interval: 1.0
llm:
  provider: openai                 # openai / deepseek / ollama / siliconflow / claude / gemini / custom
  api_url: https://api.openai.com/v1  # 留空则按 provider 直连官方
  api_key: sk-xxx
  model: gpt-4o-mini
  max_tokens: 2048                 # 单次回复最大 token
  temperature: 0.7
  system_prompt: 你是一个友好、乐于助人的机器人助手。请用简洁、自然的中文回复。
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
render:
  enabled: true                    # HTML → 图片渲染服务（可选能力；需安装 playwright 并下载浏览器）
  timeout: 30.0                    # 单次渲染超时（秒）
  format: jpeg                     # 默认输出格式: jpeg / png
  quality: 92                      # JPEG 质量（1-100；png 忽略）
  default_width: 800               # 默认渲染宽度（调用方未指定时）
  default_height: 600              # 默认渲染高度
  device_scale_factor: 1.0         # 输出清晰度倍率（如 2.0 对应 2x 高清）
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
platforms:
  telegram:                        # Telegram 平台适配器（默认关闭）
    enabled: false                 # true 启用后接入 Telegram（Bot API 长轮询）
    token: ''                      # Bot API token（@BotFather 获取）
    poll_interval: 1.0             # 长轮询间隔（秒）
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
  retention_days: 0                # 数据保留天数（messages/usage/audit/sessions 超期自动清理；0=不清理）
  record_all_messages: true        # 框架级消息记录/广播（关闭后仅内置 chat 的 LLM 对话写库）
  run_log_enabled: true            # 运行日志采集（关闭后 WebUI「运行日志」页无数据）
api_key: ''                        # API 鉴权密钥
lang: zh-CN                        # 全局语言（插件 i18n 默认语言）
```

## 进阶功能说明（功能开关均默认关闭）

| 配置节 | 功能 | 默认值 | 说明 |
|--------|------|--------|------|
| `rate_limit` | 对话限流 | `enabled: false` | 每用户每日对话上限 + 两次对话冷却间隔，超限回复提示 |
| `filter` | 敏感词过滤 | `enabled: false` | 词库为 `data/sensitive_words.txt`（一行一词，支持 `#` 注释）；词库为空时 `/filter` 命令与日志会明确提示；管理员可通过 `exempt_admins` 豁免 |
| `scheduler` | 定时任务调度器 | `enabled: true` | 调度器基座，由插件注册任务；无任务注册时零副作用 |
| `hot_reload` | 插件自动热重载 | `enabled: false` | 开发期监听 `plugins/` 目录 `.py` 文件变更并自动重载对应插件；`interval` 为轮询间隔（秒）；生产环境建议关闭 |
| `alert` | 错误告警 | `enabled: false` | 冷却窗口内 ERROR 日志达到 `error_threshold` 条时向管理员发消息告警，带 `cooldown_minutes` 冷却 |
| `image` | 图片生成 | `enabled: false` | `/image <提示词>`（或 `/画图`）命令；`image.api_key` 为空时回退 `llm.api_key`；成功后以 v12 `image` 消息段回复 |
| `render` | HTML → 图片渲染 | `enabled: true` | 基于 Playwright 无头 Chromium 将 HTML 渲染为 JPEG/PNG，供签到卡等插件复用（可选依赖 `[render]`；Chromium 下载见「启用 HTML 渲染」，国内走 npm 镜像）；未安装/浏览器缺失时自动降级不可用，`/api/bot/status` 的 `render` 字段展示能力状态 |
| `rag` | 轻量知识库 | `enabled: false` | 双模式：`keyword`（纯 Python 关键词检索，无重型依赖）/ `vector`（LanceDB 向量检索 + litellm embedding，语义更精准；需可选依赖 `lancedb`，未安装时自动回退 keyword 并告警）；开启后对话自动注入检索到的参考资料；`/kb` 命令管理文档（add/list/search/remove/reload）。vector 模式的初始化步骤见 [ARCHITECTURE.md](./ARCHITECTURE.md#向量检索rag初始化) |
| `session_summary` | 会话摘要 | `enabled: false` | 与 `llm.enable_summary` 等价，任一为 true 即启用；上下文超过条数/token 阈值时将较早消息摘要压缩，保留最近 N 轮原文 |
| `log.usage_tracking` | LLM 用量入库 | `true` | 可退出的遥测：关闭后 chat/摘要/图片不再写 usage_logs，Dashboard 用量统计将为空 |
| `llm.enable_tools` | Function Calling | `false` | 启用工具调用（内置 `get_current_time` / `random_quote`，可经 ToolRegistry 扩展）；`max_tool_rounds` 限制最大轮次（默认 5） |
| `llm.personas` | 人格/人设 | `[]` | 多组 system_prompt；聊天中 `/persona` 切换（会话级覆盖）、`/persona 列表` 查看；Web UI「LLM 配置」管理 |
| `llm.mcp_servers` | MCP 服务器 | `[]` | 连接外部 MCP 服务器（stdio/HTTP 传输），工具注册为 `mcp_{服务器名}_{工具名}` 供 LLM 调用；需开启 `enable_tools`，修改后重启 Bot 生效 |
| `llm.provider` | 提供商联动 | `openai` | 切换 provider 自动带出预设 api_url/model（openai/deepseek/ollama/siliconflow/claude/gemini/custom 共 7 个）；`api_url` 非空统一走 OpenAI 兼容协议 |
| `llm.timeout` / `llm.num_retries` | 请求超时与重试 | `60` / `2` | 单次 LLM 请求超时秒数与失败重试次数 |
| `market` | 插件市场 | `url` 默认指向 Gitee 镜像（`https://gitee.com/qingci-bot/Plugin-Market.git`，GitHub 主仓库的国内自动同步镜像，拉取更快更稳；可用 `https://github.com/Qingci-Bot/Plugin-Market.git` 切换主仓库） | WebUI「插件管理 → 插件市场」浏览/搜索/一键安装/更新/刷新；`url` 可指向自定义市场索引仓库，`mirror_url` 为索引备用源（主源拉取失败时回退），`refresh_interval` 为索引缓存 TTL（秒）；市场条目可声明 `python_requires` 版本约束，WebUI 对不兼容插件显示提示并禁用安装 |
| `platforms.telegram` | Telegram 平台适配器 | `enabled: false` | 启用后以 Bot API 长轮询接入 Telegram（`token` 由 @BotFather 获取）；事件归一化为 OneBot 12 内部模型（`type`/`detail_type`/v12 消息段），插件/命令零改动可用；回复自动路由到 Telegram；群聊 `@Bot` 可触发（支持 at 触发模式）；收发支持 v12 `image`/`voice`/`video` 段（photo → `image`、voice → `voice`、video → `video`；发送 `image`/`voice`/`video` 段分别走 `sendPhoto`/`sendVoice`/`sendVideo`，支持 file_id / URL / base64 / 本地路径）与 `reply` 回复段；成员进出群/权限变更归一化为 OneBot `notice`（`group_member_increase` / `group_member_decrease` / `group_admin_set` / `group_admin_unset`），事件插件可响应；`poll_interval` 为轮询间隔（秒） |
| `bot.log_json` | 结构化 JSON 日志 | `false` | 面向机器可读的日志采集场景 |
| `log.log_file_enabled` | 文件日志轮转 | `false` | 启用后日志写入 `log_dir/qingci-bot.log`，按 `log_file_max_bytes` 大小轮转，保留 `log_file_backup_count` 个备份 |

---

> 插件开发、API 接口、前端开发、打包详见 [PLUGIN_DEV.md](./PLUGIN_DEV.md)
>
> 独立插件开发 SDK：[Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK) — 零依赖插件开发工具包，无需克隆主项目即可开发插件

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