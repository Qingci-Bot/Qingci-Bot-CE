# 配置参考

> 面向配置：OneBot 11 / 12 协议端接入、LLM 提供商、完整 `config.yaml` 字段说明、
> 环境变量与进阶功能开关。安装与启动见 [SETUP.md](./SETUP.md)；模板文件
> [config.example.yaml](../config.example.yaml) 带字段注释，复制为 `config.yaml` 后按需修改。

## 配置 OneBot 协议端

### OneBot 11（默认）

在 OneBot 11 协议端（如 LLBot / NapCat / go-cqhttp）中添加反向 WebSocket 连接：

- 地址：`ws://127.0.0.1:3001/ws`（端口默认 3001，需与 `config.yaml` 的 `onebot.port` 保持一致）

- Access Token：留空或与 `config.yaml` 中 `onebot.access_token` 保持一致

协议端会自动携带 OneBot v11 标准的 `X-Client-Role: universal` 和 `X-Self-ID` header 连接。接入的 v11 事件会由 `bot/core/v11_compat.py` 翻译层自动归一化为 OneBot 12 事件（`type`/`detail_type`）后进入核心调度，插件侧仍能读取兼容字段（`post_type`/`message_type`/`raw_message`）。

### OneBot 12（原生，无需翻译）

创建实例时选择 `OneBot 12` 主平台（或手动设置 `platforms.onebot12.enabled: true`），在支持 OneBot 12 的协议端（如 NapCat / Lagrange.OneBot）中添加反向 WebSocket 连接：

- 地址：`ws://127.0.0.1:3002/`（端口默认 3002，需与 `config.yaml` 的 `platforms.onebot12.port` 保持一致）

- Access Token：留空或与 `config.yaml` 中 `platforms.onebot12.access_token` 保持一致（实现端以 `Authorization: Bearer` 或 `?access_token=` 携带）

事件以 OneBot 12 标准格式（`type`/`detail_type`/`message[]`）直接进入核心调度，动作以 JSON-RPC（`send_message` 等）调用——与 v11 相比省去翻译层，回复段原生 v12 表达。群管动作（`set_group_kick` 等 v11 便捷名）由 `_api_action` 映射为 `group.*` 点分命名空间（如 `group.kick`/`group.ban`）——依赖实现端支持此点分扩展约定（NapCat / Lagrange.OneBot 均支持）。

> OneBot 12 机器人在事件的 `self_id`（扁平字段）可能缺失：官方 v12 标准事件携带 `self` 对象（`{platform, user_id}`），本项目优先读取 `self_id`、缺失时回退读 `self.user_id`，无需你额外配置。

## 配置 LLM

在 Web UI 的「LLM 配置」页面填写 API 信息，或直接编辑 `config.yaml`：

```yaml
llm:
  provider: deepseek              # openai / deepseek / ollama / siliconflow / claude / gemini / custom
  api_url: https://api.deepseek.com/v1  # 留空则按 provider 直连官方
  api_key: sk-your-key
  model: deepseek-flash
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
  multimodal:                     # 图片输入（需模型支持视觉，否则会报错）
    enabled: false                #   开启后把消息里的图片解析为地址一并发送
    max_images: 4                 #   单条消息最多带几张
    max_bytes: 4194304            #   单图字节上限（本地编码时生效）
    timeout: 15                   #   解析图片（调平台 API）的超时秒数
```

**提供商联动与模型列表**：切换 `provider` 时，Web UI 会自动带出推荐的 `api_url` 与 `model`（预设见「LLM 配置」页），用户仍可覆盖为自定义值。填入 `api_key` 后，点击「获取模型」即可调用 `/api/config/llm/models` 向提供商查询可用模型列表并回填到下拉框（Ollama / Claude / Gemini / OpenAI 兼容协议均支持）。

LLM 连接测试（`/api/config/llm/test`）使用 10 秒短超时探测，不受 `timeout` 配置影响，并透传具体失败原因（鉴权 / 超时 / 网络 / 其他）。

**人格切换**：聊天中发送 `/persona 列表` 查看全部，`/persona 猫娘` 切换（仅对当前会话生效），`/persona 重置` 恢复默认人格或 `system_prompt`。

**MCP 工具**：开启 `enable_tools` 并配置 `mcp_servers` 后，启动时自动连接各服务器并将工具注册为 `mcp_{服务器名}_{工具名}` 供 LLM 调用。修改 MCP 配置后需重启 Bot 生效。

**多模态（图片输入）**：开启 `llm.multimodal.enabled` 后，聊天插件会把消息里的图片解析成模型可读的地址（`http(s)` URL 或 `data:` URI）一并发送，Web UI 的「LLM 配置」页有对应开关。默认**关闭**——纯文本模型收到图片内容会直接报错，请确认所用模型支持视觉输入后再开启（DeepSeek V4.1 Flash、GPT-4o、Claude Sonnet 5、Gemini 3.8 Flash 等均支持；具体以各家官方文档为准）。图片超过 `max_bytes` 或无法解析时自动跳过，按纯文本回复。

**provider 路由规则**（基于 litellm）：

- `api_url` 非空：统一走 OpenAI 兼容协议（`openai/{model}` + `api_base`），兼容任意 OpenAI 协议服务

- `api_url` 为空：按 provider 直连官方（`deepseek/{model}`、`ollama/{model}` 等）

- `provider: custom`：必须填 `api_url`，走 OpenAI 兼容协议

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
  event_concurrency: 16            # 同时处理的事件数上限（洪峰超出部分排队；按 CPU/LLM 并发能力调优）
  max_pending_events: 128          # 排队+执行中的事件总数上限（超过丢弃新事件，防内存无界增长）
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
  multimodal:                      # 图片输入（需模型支持视觉，否则会报错）
    enabled: false                 #   开启后把消息里的图片解析为地址一并发送
    max_images: 4                  #   单条消息最多带几张
    max_bytes: 4194304             #   单图字节上限（本地编码时生效）
    timeout: 15                    #   解析图片（调平台 API）的超时秒数
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

## 环境变量

以下变量影响运行时行为，均为可选（未设置时使用默认值）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `QINGCI_DB_POOL_SIZE` | `5` | SQLite 连接池大小；高并发读写争用时可调大 |
| `QINGCI_DB_MAX_OVERFLOW` | `10` | 连接池溢出上限 |
| `QINGCI_SKIP_MIGRATION_CHECK` | 未设置 | 设为 `1` 跳过启动期的迁移漂移检测（每次启动约省 1 秒）。打包分发 / 容器冷启动可开启，代价是模型与迁移脚本不一致时不再告警 |
| `QINGCI_DATA_HOME` | 未设置 | 可写数据根目录（`instances/`、`backend/` 的父目录）。安装版由安装向导写入，AppImage / 容器可用它把数据放到可写卷；未设置时按 `QINGCI_APP_DIR` → 源码根目录回退 |
| `QINGCI_APP_DIR` | 自动推断 | 由 Electron 壳注入，指向桌面 EXE 所在目录；源码 / Docker 部署无需设置 |

> 与 `config.yaml` 不同，环境变量在进程启动时读取，修改后需重启。

## 进阶功能说明（功能开关均默认关闭）

| 配置节                               | 功能               | 默认值                                                                                                                                                         | 说明                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| --------------------------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rate_limit`                      | 对话限流             | `enabled: false`                                                                                                                                            | 每用户每日对话上限 + 两次对话冷却间隔，超限回复提示                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `filter`                          | 敏感词过滤            | `enabled: false`                                                                                                                                            | 词库为 `data/sensitive_words.txt`（一行一词，支持 `#` 注释）；词库为空时 `/filter` 命令与日志会明确提示；管理员可通过 `exempt_admins` 豁免                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `scheduler`                       | 定时任务调度器          | `enabled: true`                                                                                                                                             | 调度器基座，由插件注册任务；无任务注册时零副作用                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `hot_reload`                      | 插件自动热重载          | `enabled: false`                                                                                                                                            | 开发期监听 `plugins/` 目录 `.py` 文件变更并自动重载对应插件；`interval` 为轮询间隔（秒）；生产环境建议关闭                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `alert`                           | 错误告警             | `enabled: false`                                                                                                                                            | 冷却窗口内 ERROR 日志达到 `error_threshold` 条时向管理员发消息告警，带 `cooldown_minutes` 冷却                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `image`                           | 图片生成             | `enabled: false`                                                                                                                                            | `/image <提示词>`（或 `/画图`）命令；`image.api_key` 为空时回退 `llm.api_key`；成功后以 v12 `image` 消息段回复                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `render`                          | HTML → 图片渲染      | `enabled: true`                                                                                                                                             | 基于 Playwright 无头 Chromium 将 HTML 渲染为 JPEG/PNG，供签到卡等插件复用（可选依赖 `[render]`；Chromium 下载见「启用 HTML 渲染」，国内走 npm 镜像）；未安装/浏览器缺失时自动降级不可用，`/api/bot/status` 的 `render` 字段展示能力状态                                                                                                                                                                                                                                                                                                                                                               |
| `rag`                             | 轻量知识库            | `enabled: false`                                                                                                                                            | 双模式：`keyword`（纯 Python 关键词检索，无重型依赖）/ `vector`（LanceDB 向量检索 + litellm embedding，语义更精准；需可选依赖 `lancedb`，未安装时自动回退 keyword 并告警）；开启后对话自动注入检索到的参考资料；`/kb` 命令管理文档（add/list/search/remove/reload）。vector 模式的初始化步骤见 [DEPLOYMENT.md](./DEPLOYMENT.md#向量检索rag初始化)                                                                                                                                                                                                                                                                          |
| `session_summary`                 | 会话摘要             | `enabled: false`                                                                                                                                            | 与 `llm.enable_summary` 等价，任一为 true 即启用；上下文超过条数/token 阈值时将较早消息摘要压缩，保留最近 N 轮原文                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `log.usage_tracking`              | LLM 用量入库         | `true`                                                                                                                                                      | 可退出的遥测：关闭后 chat/摘要/图片不再写 usage\_logs，Dashboard 用量统计将为空                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `llm.enable_tools`                | Function Calling | `false`                                                                                                                                                     | 启用工具调用（内置 `get_current_time` / `random_quote`，可经 ToolRegistry 扩展）；`max_tool_rounds` 限制最大轮次（默认 5）                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `llm.personas`                    | 人格/人设            | `[]`                                                                                                                                                        | 多组 system\_prompt；聊天中 `/persona` 切换（会话级覆盖）、`/persona 列表` 查看；Web UI「LLM 配置」管理                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `llm.mcp_servers`                 | MCP 服务器          | `[]`                                                                                                                                                        | 连接外部 MCP 服务器（stdio/HTTP 传输），工具注册为 `mcp_{服务器名}_{工具名}` 供 LLM 调用；需开启 `enable_tools`，修改后重启 Bot 生效                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `llm.provider`                    | 提供商联动            | `openai`                                                                                                                                                    | 切换 provider 自动带出预设 api\_url/model（openai/deepseek/ollama/siliconflow/claude/gemini/custom 共 7 个）；`api_url` 非空统一走 OpenAI 兼容协议                                                                                                                                                                                                                                                                                                                                                                                                       |
| `llm.timeout` / `llm.num_retries` | 请求超时与重试          | `60` / `2`                                                                                                                                                  | 单次 LLM 请求超时秒数与失败重试次数                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `market`                          | 插件市场             | `url` 默认指向 Gitee 镜像（`https://gitee.com/qingci-bot/Plugin-Market.git`，GitHub 主仓库的国内自动同步镜像，拉取更快更稳；可用 `https://github.com/Qingci-Bot/Plugin-Market.git` 切换主仓库） | WebUI「插件管理 → 插件市场」浏览/搜索/一键安装/更新/刷新；`url` 可指向自定义市场索引仓库，`mirror_url` 为索引备用源（主源拉取失败时回退），`refresh_interval` 为索引缓存 TTL（秒）；市场条目可声明 `python_requires` 版本约束，WebUI 对不兼容插件显示提示并禁用安装                                                                                                                                                                                                                                                                                                                                                          |
| `platforms.telegram`              | Telegram 平台适配器   | `enabled: false`                                                                                                                                            | 启用后以 Bot API 长轮询接入 Telegram（`token` 由 @BotFather 获取）；事件归一化为 OneBot 12 内部模型（`type`/`detail_type`/v12 消息段），插件/命令零改动可用；回复自动路由到 Telegram；群聊 `@Bot` 可触发（支持 at 触发模式）；收发支持 v12 `image`/`voice`/`video` 段（photo → `image`、voice → `voice`、video → `video`；发送 `image`/`voice`/`video` 段分别走 `sendPhoto`/`sendVoice`/`sendVideo`，支持 file\_id / URL / base64 / 本地路径）与 `reply` 回复段；成员进出群/权限变更归一化为 OneBot `notice`（`group_member_increase` / `group_member_decrease` / `group_admin_set` / `group_admin_unset`），事件插件可响应；`poll_interval` 为轮询间隔（秒） |
| `bot.log_json`                    | 结构化 JSON 日志      | `false`                                                                                                                                                     | 面向机器可读的日志采集场景                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
