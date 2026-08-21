# Qingci-Bot CE 架构

## 系统架构

```
┌──────────┐  OneBot 11 WS  ┌──────────────────────────────────────────┐   HTTP/WS   ┌──────────┐
│  OneBot  │ ◄────────────► │            Qingci-Bot CE                │ ◄─────────► │  Web UI  │
│(OneBot 11)│  收发消息/事件  │  ┌──────────────────────────────────┐   │   API 推送   │  (管理端)  │
└──────────┘                 │  │ aiocqhttp (反向 WS 服务端)        │   │            └──────────┘
                             │  ├──────────────────────────────────┤   │
                             │  │ v11_compat (v11 事件 → v12 翻译) │   │
                             │  ├──────────────────────────────────┤   │
                             │  │ Telegram (长轮询 → OneBot 12 事件)│   │
                             │  ├──────────────────────────────────┤   │
                             │  │ Dispatcher (OneBot 12 事件模型)   │   │
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

1. **OneBot 11 协议端**（如 LLBot / NapCat / go-cqhttp）通过 OneBot 11 反向 WebSocket 连接至 Qingci-Bot CE；Telegram 由 `telegram.py` 适配器以 Bot API 长轮询接入
2. **aiocqhttp** 收到 OneBot 11 事件，经 **v11_compat**（`bot/core/v11_compat.py`）翻译为 **OneBot 12** 事件（`type`/`detail_type`/`message[]`）；**telegram 适配器**直接将原生更新归一化为 OneBot 12 事件（注入 `platform` 字段），统一分发至 **Dispatcher**
3. **Dispatcher** 以 OneBot 12 事件模型解析出 `MessageContext`（`Message.from_raw` 自动归一化 v11/v12 消息段），按 priority 调度 **PluginManager** 中的 Matcher，匹配 Rule/Permission 后执行 handler
4. 未匹配则回退到旧式 `on_message`；内置 chat 插件调用 **LLMManager** 生成回复
5. 回复按 **MessageContext.platform** 路由到对应 **PlatformAdapter**，最终发送回 OneBot 协议端（QQ 用户）或 Telegram 对话；发送层消费 OneBot 12 标准消息段（`send_message` 动作的 `message` 参数），由各适配器映射为平台私有格式
6. **Web UI** 通过 HTTP/WebSocket 与 API 服务通信，管理配置、插件、日志等（侧边栏展示各平台实时连接状态）

> **多平台原则**：事件在入口归一化为 OneBot 12、回复在出口路由回平台，插件/命令对来源平台完全无感知——一套 Matcher/Rule/Permission 逻辑基于统一事件模型天然支持所有已接入平台。

### 协议层归属

插件系统的协议层（`PluginBase` / `Matcher` / `MatcherContext` / `Permission` / `Rule` / `MessageContext` / `RateLimiter`）**统一由独立插件 SDK 维护**（[Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK) 的 `qingci_plugin_sdk` 包）。主项目的 `bot/plugin/{base,matcher,permission,rule,ratelimit}.py` 与 `bot/core/dispatcher.py` 中的 `MessageContext` 均为薄转发（`from qingci_plugin_sdk.* import *`），不保存任何协议层实现，从根本上消除两处定义漂移：

- 内置插件与外部插件使用**同一个**基类与匹配器体系，行为完全一致
- 修改权限语义、匹配规则等协议时只改 SDK 一处，主项目无需同步
- SDK 是主项目的**正式依赖**（`pyproject.toml` 声明 git 依赖；构建/本地开发由 `build.ps1` 以 `-e` 安装优先）

## 进程模型与外壳边界（无头内核库化方向）

Qingci-Bot-CE 采用**单进程 = 单 Bot 实例**（`set_bot` 重复注册有告警防护）。`main.py` 为统一入口，同一进程内承载 Bot 内核 + FastAPI/WebUI + 桌面后端：

- **CLI（默认）**：`python main.py` — Bot + API 同事件循环（`run_bot_and_api`）
- **API-only**：`python main.py --no-bot` — 仅 Web UI/API（`--api-only` 独立进程方向的雏形）
- **桌面**：`python main.py --desktop` — pywebview 窗口 + 系统托盘，后端在后台线程运行

内核边界约束：

- `bot/` 内核**不反向依赖** `desktop/`（0 引用）；桌面通过 `main.run_bot_and_api` 复用同一启动序列
- 唯一例外：`bot/plugin/manager.py` 与 `bot/plugin/webapi.py` 引用 `api.auth`（插件 Web 页面 / API 的鉴权）。`api/auth.py` 是纯函数模块（不持有 FastAPI 应用实例），该引用仅用于鉴权判断，不构成进程模型耦合；内核库化时可将鉴权抽象为回调注入解除
- 无头方向：`bot/` 可独立为库（不启用桌面、未装 `[render]` 时渲染能力自动降级），CLI / API / 桌面三外壳复用同一内核装配（`composition.assemble_bot`）

数据保留：`config.log.retention_days`（默认 0 不清理）由 Bot 每日清理 `messages` / `sessions` / `usage_logs` / `audit_logs` 中超期记录（`bot/core/bot.py` `_data_retention_loop`），防止长期运行单表无限膨胀。

## 项目结构

```
Qingci-Bot-CE/
├── main.py                    # 统一入口（实例启动 / 单实例保护 / 跨进程重启）
├── pyproject.toml
├── alembic.ini                # Alembic 迁移配置
├── config.example.yaml        # 配置模板（脱敏；实例配置在 instances/<name>/config.yaml）
├── build.ps1                  # PyInstaller 打包脚本
├── qingci-bot-ce.spec         # PyInstaller 打包配置
├── bot/
│   ├── config.py              # 配置管理（Pydantic 模型）
│   ├── instances.py           # 实例管理（instances/<name>/ 自包含目录，含 config/plugins/data）
│   ├── paths.py               # 路径解析（app_root / data_root / plugins_dir）
│   ├── i18n.py                # 国际化翻译器（框架侧）
│   ├── alerter.py             # 错误告警器（ERROR 日志阈值 → 私聊通知管理员）
│   ├── filter.py              # 敏感词过滤器（词库 + 打码）
│   ├── broadcast.py           # 消息广播（WS 实时推送 broker）
│   ├── logformat.py           # 结构化 JSON 日志 + 文件轮转 + 运行日志采集（RunLogHandler 环形缓冲）
│   ├── html_renderer.py       # HTML → 图片渲染服务（Playwright 无头 Chromium，可选依赖）
│   ├── core/
│   │   ├── bot.py             # Bot 主类（生命周期、事件调度、全局钩子）
│   │   ├── composition.py     # 组合根（assemble_bot：组件装配 + DI 注册）
│   │   ├── v11_compat.py      # OneBot 11 事件 → OneBot 12 事件翻译层（双模归一化入口）
│   │   ├── platforms/         # 多平台适配器（base.py PlatformAdapter 契约 + onebot11.py + onebot12.py + telegram.py）
│   │   ├── connection.py      # 兼容再导出（实际实现在 platforms/onebot11.py）
│   │   ├── dispatcher.py      # 消息分发 + Matcher 调度（MessageContext 引 protocol.context）
│   │   ├── message.py         # 类型化消息构造器（Message/MessageSegment，仅 OneBot-11 平台兼容用；v12 消息段由 SDK segments 承担）
│   │   ├── scheduler.py       # 定时任务调度器
│   │   ├── tasks.py           # 后台任务管理（防 GC + 停机等待）
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
│   │   └── knowledge.py       # 知识库（keyword 关键词检索 + vector 向量检索，后者需 lancedb）
│   ├── db/
│   │   ├── database.py        # 数据库仓储（基于 SQLModel）
│   │   ├── engine.py          # 异步引擎 + 会话工厂（WAL 模式）
│   │   └── models.py          # SQLModel 模型定义
│   ├── testing/               # 插件测试工具（TestBot + 事件构造器，无需启动真实 Bot）
│   │   ├── bot.py             # TestBot 轻量测试环境
│   │   └── events.py          # 事件构造器（v11 / v12 双模）
│   └── plugin/
│       ├── protocol/           # 插件协议层（薄转发 SDK，唯一实现来源为 Plugins-SDK）
│       │   ├── base.py         # PluginBase / PluginStatus
│       │   ├── context.py      # MessageContext
│       │   ├── matcher.py      # Matcher / MatcherContext / 工厂函数
│       │   ├── rule.py         # Rule 规则系统
│       │   ├── permission.py   # Permission 权限系统
│       │   ├── ratelimit.py    # RateLimiter 限流
│       │   ├── session.py      # Session 会话阶梯
│       │   └── events.py       # 类型化事件（notice/request）
│       ├── base.py             # 兼容再导出（指向 protocol/base.py）
│       ├── manager.py          # 插件管理器（热加载 + 模块级收集 + SDK data_root 重定向）
│       ├── market.py           # 插件市场：索引拉取/缓存 + 安装/更新编排
│       ├── deps.py             # 插件第三方依赖自动安装（data_root()/deps + sys.path 注入）
│       ├── _proc.py            # 子进程公共标志（Windows 隐藏控制台窗口）
│       ├── ssrf.py             # SSRF 防护（插件网络请求目标校验）
│       ├── matcher.py          # 兼容再导出（指向 protocol/matcher.py）
│       ├── rule.py             # 兼容再导出（指向 protocol/rule.py）
│       ├── permission.py       # 兼容再导出（指向 protocol/permission.py）
│       ├── ratelimit.py        # 兼容再导出（指向 protocol/ratelimit.py）
│       ├── session.py          # 兼容再导出（指向 protocol/session.py）
│       ├── events.py           # 兼容再导出（指向 protocol/events.py）
│       ├── webapi.py           # 插件级 Web API 适配器（register_api → /api/plugin-web/<name>/）
│       ├── llm_tool.py         # @llm_tool 插件级 LLM 工具声明（含注册到 ToolRegistry 的运行时逻辑）
│       ├── watcher.py          # 插件自动热重载监听
│       └── builtin/            # 内置插件（目录结构）
│           ├── chat/          # LLM 对话（Matcher API）
│           ├── admin/         # 管理命令（含 /filter /group）
│           ├── help/          # /help 命令（按权限列出可用命令）
│           ├── imagegen/      # AI 绘图（/image 命令）
│           └── knowledge/     # 知识库管理（/kb 命令）
├── plugins/                   # 外部插件目录（Bot 启动时自动扫描加载；实例模式下为 instances/<name>/plugins）
│   ├── __init__.py
│   ├── _template/             # 插件开发模板（以 _ 开头，不自动加载）
│   │   ├── __init__.py
│   │   └── plugin.json        # 插件元数据模板
│   └── hello/                 # 最小示例插件
│       └── __init__.py
├── instances/                 # 实例注册表（运行时生成；每个实例一个自包含目录，无全局模式）
│   └── <name>/                # config.yaml + plugins/ + data/（DB/日志/插件数据）
├── migrations/                # Alembic 迁移脚本
│   ├── env.py                 # 异步迁移环境
│   └── versions/              # 迁移版本
├── api/
│   ├── auth.py                # API 鉴权
│   ├── audit.py               # 审计日志（埋点 + 查询）
│   ├── server.py              # FastAPI 应用
│   └── routes/                # API 路由（bot/config/plugin/market/log/group/login/backup/command/instances）
├── web/                       # Vue 3 前端
│   └── src/
│       ├── views/             # 页面组件
│       ├── components/        # 通用组件（Drawer 等）
│       ├── stores/            # Pinia 状态管理
│       ├── router/            # 路由配置
│       └── styles/            # 全局样式
├── desktop/
│   ├── app.py                  # 桌面入口（run_desktop）
│   ├── splash.py              # 启动画面（即时加载，重型模块延迟导入）
│   ├── tray.py                # 系统托盘
│   ├── single_instance.py     # 单实例保护（Windows 命名互斥量，由数据根目录派生）
│   ├── relaunch.py            # 跨进程重启助手（切换/重命名实例后重启）
│   └── app-icon.ico           # 应用图标（exe 图标 + 托盘图标）
└── data/                      # 可写数据根目录（实例模式下默认 instances/<name>/data；--data-dir 可覆盖）
    └── qingci-bot.db          # SQLite 数据库文件
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.12 + FastAPI + uvicorn |
| 平台接入 | 内核统一 OneBot 12 事件模型；输入含 OneBot 11（aiocqhttp 反向 WS，v11_compat 翻译层）+ OneBot 12（原生反向 WS，事件直通）+ Telegram（Bot API 长轮询），统一 `PlatformAdapter` 契约 |
| LLM | litellm (统一接口，延迟导入加速启动) |
| MCP | mcp (Model Context Protocol，stdio/HTTP) |
| 数据库 | SQLModel + Alembic + aiosqlite (WAL 模式) |
| 定时任务 | APScheduler |
| 插件协议层 | qingci_plugin_sdk（PluginBase/Matcher/Rule/Permission，主项目薄转发） |
| 前端 | Vue 3 + Vite + Pinia |
| 桌面 | PyWebView + pystray |

## 核心模块

### 组合根（`bot/core/composition.py`）

`QingciBot.__init__` 不再手写组件装配：`assemble_bot(bot)` 集中创建全部核心服务（DB/连接/分发器/LLM/插件管理器/DI/会话状态/事件总线/限流器/调度器/工具注册表/知识库/敏感词过滤器）并注册进 DI 容器。`__init__` 只保留配置加载与状态字段初始化。测试可通过注入 fake 实现替换组件，或直接调用 `build_bot()` 便捷入口。

### Bot 主类（`bot/core/bot.py`）

生命周期管理、事件调度、全局钩子（前置/后置中间件）、事件并发限流（Semaphore）。`set_bot()` 将 bot 的 DI 容器登记为进程级引用，`get_bot()` 通过 `container.resolve_sync(QingciBot)` 解析实例，不持有模块级 bot 单例。

### Dispatcher（`bot/core/dispatcher.py`）

- 收集所有已注册 Matcher，按 priority 升序排序
- 依次检查 Rule + Permission，命中则执行 handler
- 无 Matcher 匹配时回退到旧式 `on_message`
- 支持 block 控制是否继续后续 Matcher
- `MessageContext` 由 SDK 提供（字段与主项目历史版本完全一致，另含 `sender_name` 属性与 `platform` 来源平台字段）

### 多平台适配器（`bot/core/platforms/`）

平台协议统一收敛为 `PlatformAdapter` 契约，插件对来源平台无感知；事件归一化为 **OneBot 12 内部模型**（`type`/`detail_type` + 标准消息段，注入 `platform` 字段），回复按 `MessageContext.platform` 路由回对应适配器：

- **`base.py`**：定义 `PlatformAdapter` 契约（适配器名/展示名、启动与关闭、事件上报回调、发送消息、`get_status`/API 透传等），任何平台只需实现该契约即可接入
- **`telegram.py`**：Telegram 平台实现——以 Bot API 长轮询（`getUpdates`）接入，由 `platforms.telegram.enabled/token/poll_interval` 控制；收到更新后归一化为 **OneBot 12 消息事件**并注入 `platform: "telegram"`。关键能力：① 群聊解析 `entities`（`mention` / `text_mention`）识别 `@Bot`，命中时写入 v12 `mention` 段（`user_id=self_id`）并置 `is_at_bot`，确保 at 触发模式在 Telegram 群聊生效（私聊由 SDK 规则天然放行）；② `photo` / `image/*` document 归一化为 `image` 段（`file_id`）+ `images`，`voice` → `voice`、`video` / `video_note` → `video` 段，消息 `sub_type` 语义对齐 OneBot（私聊 `friend`）；③ 发送消费 OneBot 12 标准段——`image` → `sendPhoto`、`voice` → `sendVoice`、`video` → `sendVideo`（file_id / http(s) URL / `base64://` / `data:` / 本地路径，本地与 base64 走 multipart 上传，caption 附着首条媒体），`reply` 段 → 回复指定消息，其余不可渲染的段降级为纯文本并合并连续空白，之外走 `sendMessage`；④ `chat_member` / `my_chat_member` 成员变动归一化为 OneBot `notice`（`group_member_increase` / `group_member_decrease` / `group_admin_set` / `group_admin_unset`，被邀请 `sub_type=invite`），由既有事件 Matcher 消费；⑤ 轮询 offset 采用**整批确认**：先以 `max(update_id)+1` 推进游标再并发消费本批更新，单条处理失败也已被确认，避免失败更新无限重放
- **`OneBotConnection`**（`bot/core/platforms/onebot11.py`，`bot/core/connection.py` 保留兼容再导出）作为「onebot」平台接入 OneBot 11 反向 WebSocket，收到 v11 事件先经 `v11_compat.v11_event_to_v12()` 翻译为 v12 事件再上报（同时保留 `platform`/兼容字段），与原反向 WS 行为兼容，回复路由与附加平台共用同一发送映射
- **`onebot12.py`**：OneBot 12 平台实现——aiohttp 反向 WebSocket 服务端（`platforms.onebot12.enabled/host/port/access_token` 控制），支持 OneBot 12 实现端（NapCat / Lagrange.OneBot 等）原生接入；事件以 v12 标准格式直通 Dispatcher（注入 `platform: "onebot12"`），动作以 JSON-RPC（`{action, params, echo}`）请求并通过 echo 匹配响应，`send_message` 动作消费 v12 段数组原生表达，与 v11 相比省去翻译层
- 附加平台（Telegram / OneBot 12）启动失败仅记录日志，不阻断主平台（OneBot）可用性

### 事件模型（OneBot 12 内核）

核心统一消费 **OneBot 12 事件模型**——事件以 `{type, detail_type, ...}` 标识（`message` / `notice` / `request` / `meta`），消息以标准 `{type, data}` 段数组表达，媒体统一用 `file_id` 引用。为兼顾存量 OneBot 11 输入与旧插件兼容，采用**双模归一化**：

- **`bot/core/v11_compat.py`**：纯函数翻译 v11 事件 → v12 事件。`message_type` → `detail_type`、`raw_message` → `alt_message`、`post_type` → `type`，ID 字段字符串化；notice 按 `notice_type`+`sub_type` 细分（`group_increase` → `group_member_increase`、`group_admin`+`set` → `group_admin_set`、`group_ban`+`lift_ban` → `group_member_unban` 等），与 SDK 事件映射表对称。无法识别的事件类型原样返回（防御性，不丢事件）
- **`MessageContext`**（SDK `context.py`）：`from_v12_event` 以 v12 事件构造上下文，保留 v11 兼容字段（`post_type` / `message_type` 由 v12 字段派生），`post_type`/`message_type`/`raw_message` 供存量插件继续读取；`segments` 统一存 v12 标准段
- **消息段双向转换**（SDK `segments.py`）：`Message.from_raw` 嗅探段数组，v11 段（`at`/`at_all`/`record`/`face`/`forward`/`reply: id`）自动归一化为 v12（`mention`/`mention_all`/`voice`/`text`/`reply: message_id`）；`segments_to_v11`/`as_v11_segments()` 提供 v11 兼容视图
- **类型化事件**（SDK `events.py`）：notice/request 解析同时接受 v11（`notice_type`）与 v12（`detail_type`）两种事件 dict，v12 `detail_type` 自动映射回 v11 命名空间，插件侧事件类（`GroupIncreaseNotice` 等）保持不变

### PluginManager（`bot/plugin/manager.py`）

- 插件热加载/卸载/重载（"先建后拆"策略）
- 模块级装饰器自动收集（`on_command` 等）
- 依赖解析（require），循环依赖检测
- 路径白名单：仅允许 `plugins.*` 和 `bot.plugin.builtin.*`
- 加载 SDK 式插件时调用 `qingci_plugin_sdk.paths.set_data_root()` 将插件数据目录重定向到当前实例（协议层转发后，内置插件同样经此路径，实例隔离一致）

### LLMManager（`bot/llm/manager.py`）

- 会话管理：按群聊/用户独立维护对话历史，内存 + 数据库双写
- Token 裁剪：按条数与 Token 双重限制，超出自动裁剪
- 会话摘要：可选的摘要压缩，保留最近 N 轮原文
- 人格切换：`/persona` 命令会话级覆盖 system_prompt

### HTML 渲染服务（`bot/html_renderer.py`）

基于 Playwright 无头 Chromium 将 HTML 渲染为 JPEG/PNG，供签到卡等「HTML 模板 → 图片消息」插件复用：

- 可选依赖（`[render]` 分组，含 playwright；需 `playwright install chromium` 下载浏览器），懒加载——未安装/浏览器缺失时渲染能力自动降级不可用（`render_html()` 抛 `HtmlRenderUnavailableError`），框架启动不受影响
- 浏览器惰性启动并复用（进程内单例）；渲染超时（`render.timeout`）控制、失败自动重建浏览器；`close()` 幂等，Bot 停止时自动关闭
- 能力探测 `probe()` 实际启动一次浏览器验证并缓存；`GET /api/bot/status` 的 `render` 字段暴露 `enabled`/`supported`/`available`/`reason`，插件经 `bot.html_renderer` 访问
- 配置节 `render`：`enabled`/`timeout`/`format`（jpeg/png）/`quality`/`default_width`/`default_height`/`device_scale_factor`（输出清晰度倍率）

### Database（`bot/db/`）

- SQLModel 模型定义 + Alembic 迁移管理
- aiosqlite 异步引擎（WAL 模式，提升并发读性能）
- 批量写入优化（`save_messages_batch`）
- 在线备份（sqlite backup API）

## 生产环境部署

### 向量检索（RAG）初始化

向量检索模式基于 LanceDB（嵌入式向量数据库）+ litellm embedding，无需额外服务端部署，但需要完成以下初始化步骤：

> **依赖**：向量模式需要 `lancedb`（可选依赖，`pip install -e ".[vector]"` 或 `uv pip install lancedb`）。未安装时 `mode: vector` 自动回退为关键词检索并输出警告，Bot 不会崩溃。

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
- **文档变更自动重建**：通过 `/kb` 命令添加/删除文档时，自动触发全量索引重建
- **手动触发重建**：`/kb reload` 重新索引知识库目录（知识库无独立 Web 管理页，统一经 `/kb` 命令管理）

**首次索引耗时估算**：取决于文档总量和 embedding API 响应速度。100 篇中等长度文档约需 30-60 秒，期间 Bot 其他功能不受影响。

#### 4. 索引存储位置

LanceDB 数据存储在 `data/knowledge/.lancedb/` 目录下，无需额外维护。如需迁移或备份，直接复制整个 `.lancedb/` 目录即可。

#### 5. 验证索引状态

通过 `/kb list` 命令可查看：知识库文档列表与分块状态（`/kb reload` 可手动重建索引）。

#### 注意事项

- **embedding API 费用**：每次全量索引重建会调用 embedding API，文档量大时请注意 API 调用成本。日常增量添加文档也会触发全量重建（当前版本未实现增量索引）
- **embedding 模型一致性**：索引重建时使用的 embedding 模型必须与检索时一致，否则向量维度不匹配会导致检索失败
- **降级到 keyword 模式**：若 embedding API 不可用，可将 `mode` 改回 `keyword`，立即回退到关键词检索模式，无需重建索引；若 `lancedb` 未安装，程序自动回退到 keyword 并告警

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

### 协议端断连恢复

Qingci-Bot CE 启动后会自动监测协议端（OneBot 11 客户端）的 WebSocket 连接状态。若协议端异常退出或重启：

- **自动重连**：检测到断连后，以指数退避策略（初始 1s，上限 60s）自动尝试重连，无需手动干预
- **优雅降级**：断连期间 Web UI 与 API 服务正常可用，Bot 状态显示为"未连接"
- **重连成功**：自动恢复消息收发，已加载插件与配置保持不变

断连监控与重连回调内置于 `bot/core/platforms/onebot11.py`，无需额外配置。
