# Changelog

All notable changes to Qingci-Bot CE will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — 1.8.0（OneBot 12 迁移）

### Added
- **OneBot 12 内核**：内部事件模型全面迁移为 OneBot 12——事件以 `type` / `detail_type` 标识（`message`/`notice`/`request`/`meta`），消息以标准 `{type, data}` 段数组表达（媒体统一 `file_id` 引用），为后续跨平台开发奠定统一基础
- **SDK 消息段抽象（`segments.py`）**：新增 `SegmentType` 常量、`MessageSegment` 工厂（`text`/`mention`/`mention_all`/`image`/`voice`/`audio`/`video`/`file`/`reply`/`location`）、`Message` 容器（纯文本提取/双模嗅探），以及 v11↔v12 段双向转换（`normalize_v11_segment`/`to_v11_segment`/`segments_to_v11`/`segments_to_v12`）
- **SDK 双模事件上下文（`context.py`）**：新增 `MessageContext.from_v12_event`，以 v12 事件构造上下文并派生 v11 兼容字段；`segments` 统一存 v12 标准段，`as_v11_segments()` 提供兼容视图
- **SDK 双模类型化事件（`events.py`）**：notice/request 解析同时接受 v11（`notice_type`）与 v12（`detail_type`）事件 dict，v12 `detail_type` 自动映射回 v11 命名空间，插件侧事件类（`GroupIncreaseNotice` 等）保持不变
- **v11 事件翻译层（`bot/core/v11_compat.py`）**：纯函数将 OneBot 11 事件翻译为 OneBot 12（`message_type`→`detail_type`、`raw_message`→`alt_message`、ID 字符串化；notice 按 `notice_type`+`sub_type` 细分），无法识别类型原样返回（防御性不丢事件）
- **v11 适配器翻译接入**：`OneBotConnection`（aiocqhttp 反向 WS）收到 v11 事件先经 `v11_event_to_v12()` 归一化为 v12 再上报，核心只消费 v12 事件模型；对存量插件保留 `post_type`/`message_type`/`raw_message` 兼容字段
- **Telegram 适配器 v12 化**：`telegram.py` 归一化为 OneBot 12 事件（`mention` 段而非 `at`、`voice` 段而非 `record`），发送直接消费 v12 段数组（`image`/`voice`/`video` 段映射 `sendPhoto`/`sendVoice`/`sendVideo`）；成员变动归一化为 `group_member_increase`/`group_member_decrease`/`group_admin_set`/`group_admin_unset`
- **测试事件构造器双模**：`bot/testing/events.py` 新增 `make_v12_message_event`/`make_v12_notice_event`/`make_v12_request_event`，与 v11 构造器并存，供迁移后核心路径测试使用
- **OneBot 12 回复段判别修复**：`Message.from_raw` 对 reply 段按字段判别（`message_id`/`id`），避免 v12 reply 段被误归一化（修复方案 A 迁移中发现的关键兼容问题）
- **实例绑定主平台**：创建实例时可选择绑定主平台（`onebot` / `telegram`，`POST /api/instances` 新增 `platform` 字段，WebUI 实例创建表单新增平台下拉）——实例元数据记录 `platform`，创建时自动渲染对应适配器启用配置：OneBot 主平台启动反向 WS 服务端（`onebot.enabled: true`）、Telegram 主平台关闭反向 WS 并启用 Telegram 适配器，实现针对平台的系统设置落位
- **`onebot.enabled` 开关**：`OneBotConfig` 新增 `enabled` 字段控制反向 WS 服务端启停（默认 `true`），`assemble_bot()` 按配置条件启动 OneBot 适配器，避免 Telegram 主平台实例空跑无用服务
- **权限/黑名单标识字符串化**：`bot.super_admin` / `bot.admin_users` / `group_blacklist` / `user_blacklist` 由数字 ID 升级为平台无关字符串（支持 QQ 号、Telegram 用户 ID 等任意平台 ID）；`before-validator` 自动将存量数字配置转为字符串，旧 config.yaml 无需手动迁移；`/blacklist` 命令、首次向导、WebUI 系统设置同步改为字符串输入
- **SDK 权限函数支持字符串 ID**：`USER` / `GROUP_MEMBER` 接受 `int` / `str` / 混合序列，内部归一化为字符串比较，与 OneBot 12 字符串 ID 语义对齐（见 Plugins-SDK 变更记录）

### Changed\n- **消息发送兼容**：`send_msg`/`send_group_msg`/`send_private_msg`/`call_api` 的 `message` 参数统一接受纯文本 / v11 段数组 / v12 段数组；发往 OneBot-11 协议端时由 `segments_to_cq` 将 v12 段自动转 CQ 码（`mention`→`at`、`voice`→`record` 等）\n- **平台状态按实例隔离**：`GET /api/bot/status` 的 `platforms` 列表仅上报已启用的适配器（`enabled=False` 跳过）——Telegram 主平台实例不再混入已停用的 OneBot 状态；WebUI 侧边栏平台状态按当前激活实例主平台过滤展示\n- **系统设置按实例平台渲染**：WebUI 系统设置按当前实例主平台条件渲染连接配置——OneBot 实例仅显示「OneBot 连接」卡片，Telegram 实例仅显示「Telegram 连接」卡片（原同时展示两套平台配置）\n- **实例管理独立页面**：侧边栏实例列表移至独立「实例管理」页面（`/instances`），侧边栏导航新增该入口；侧边栏仅保留平台连接状态指示\n- **文档同步**：README / ARCHITECTURE / PLUGIN_DEV 全面更新——产品定位改为 OneBot 12 内核 + 多平台，架构图补充 v11_compat 翻译层与事件模型章节，插件开发指南推荐 SDK v12 消息段（v11/CQ 路径标注为平台专用）

## [1.7.0] - 2026-08-17

### Added
- **插件市场切换源 / 自定义源**：后端新增 `GET/PUT /api/plugins/market/source`——切换市场源时先持久化 `market.url`，清空旧源的内存与磁盘索引缓存（`MarketClient.clear_disk_cache()`），立即拉取新源校验，源不可用时自动回滚到原源并提示；WebUI 插件市场新增「切换源」面板，可查看当前源、输入自定义 git / HTTP 索引地址，并一键恢复官方默认（`DEFAULT_MARKET_URL`）
- **Web 关于页版本号动态化**：关于页版本号由硬编码改为从后端状态接口动态读取——`bot/__init__.py` 的 `__version__` 为唯一版本来源，`api/server.py`、`bot/core/bot.py`、`web/package.json` 与关于页统一引用，消除多处置 1.5.1/1.6.0 漂移
- **Web 关于页鸣谢清单补全**：对照 `pyproject.toml` 依赖逐项核对，补齐 `pywebview` / `pystray`（桌面端运行依赖）鸣谢
- **Telegram 增强：@提及触发与图片收发**：群聊解析 `entities`（`mention` / `text_mention`）识别 `@Bot`，命中时写入 `at` 段（`qq=self_id`）并置 `is_at_bot`，at 触发模式在 Telegram 群聊生效（私聊由 SDK 规则天然放行）；`photo` / `image/*` document 归一化为 `image` 段 + `images`（file_id）；发送侧 `send_msg` / `call_api` 识别 `[CQ:image]` → `sendPhoto`（支持 Telegram file_id / http(s) URL / `base64://` / `data:` / 本地路径，本地与 base64 走 multipart 上传，首图带 caption），其余不可渲染的 CQ 段降级为纯文本并合并连续空白；新增 14 个用例，`telegram.py` 覆盖升至 64%
- **Telegram 增强：成员变动通知与轮询可靠性**：`chat_member` / `my_chat_member` 成员变动归一化为 OneBot `notice` 事件（`group_increase` / `group_decrease` / `group_admin`，被邀请 `sub_type=invite`），由原有事件 Matcher 消费；轮询 offset 改为处理单条更新后推进，单条处理失败仅记录并仍推进，避免失败更新无限重放；补充 5 个用例
- **Telegram 增强：语音/视频媒体与 CQ 回复**：接收侧 `voice` → `record`、`video` / `video_note` → `video` 段；发送侧识别 `[CQ:record]` → `sendVoice`、`[CQ:video]` → `sendVideo`（与图片共用 file_id / http(s) URL / `base64://` / 本地路径解析），`[CQ:reply,id=N]` → `reply_to_message_id` 回复指定消息；消息事件 `sub_type` 语义修正（私聊 `friend`）；新增 8 个用例

### Changed
- **Telegram 轮询改为有限并发消费**：引入 `asyncio.Semaphore`（`_MAX_CONCURRENT_UPDATES=8`）控制单批更新内的并发度，慢更新不再阻塞同批其他更新，同时避免无限并发；offset 改为整批确认（`max(update_id)+1`）后再消费，单条处理失败仅记日志且仍被确认，杜绝同一 `update_id` 失败后无限重放
- **Telegram API 调用错误分类**：新增 `TelegramAPIError` 及其子类（`TelegramUnauthorizedError`=401 / `TelegramForbiddenError`=403 / `TelegramNotFoundError`=404），`_api` 按 Telegram `error_code` 归类抛出，网络/超时等 HTTP 异常统一包装为 `TelegramAPIError`，便于上层区分 Token 失效、被禁言、目标不可达等场景
- **Telegram Bot Token 热更新**：新增 `TelegramAdapter.set_token(token)`，运行时更新 `self.token` 无需重启适配器，下次 API 调用即生效（空值拒绝并抛 `ValueError`）；适配器的随附测试新增 7 个用例
- **Telegram 轮询自适应退避与恢复探测**：连续失败按指数退避（`_BACKOFF_MIN=1.5s` 起步、封顶 `_BACKOFF_MAX=60s`），恢复后自动回到轮询间隔；判定离线（连续 ≥5 次失败触发 `notify_disconnected`）后一旦成功即广播 `notify_reconnected` 并从断连恢复，减少空闲期无效轮询开销
- **Telegram 长轮询可观测性增强**：新增 `status_info()`（合并进 `GET /api/bot/status` 的 platforms 项）暴露 `connection_state`（connected/connecting/stopped）、`consecutive_errors`、`error_count`、`last_error_time`、`last_disconnect_time`、`identity_dirty`、`backoff`；`get_status` 统一追加 `**p.status_info()`（base 默认 `{}`）
- **Telegram HTTP 超时/重试可配置化**：`TelegramAdapter` 新增 `request_timeout`（默认 `POLL_TIMEOUT+10=40s`，须大于长轮询 timeout）与 `max_retries`（默认 0，仅对 `httpx.TransportError` 网络层错误有限重试，业务错误绝无重试避免发送类重复），经 `platforms.telegram` 配置节透传入 `make_platform`
- **Telegram Token 热更新后自动重验身份**：`set_token()` 更新后置 `_identity_dirty`，轮询下一轮自动调用 `getMe` 刷新 `self_id`/`username`（新增 `refresh_identity()`，`start()` 亦复用）；重验失败仅记日志下轮再试，不中断轮询
- **版本号统一用脚本管理**：新增 `scripts/bump_version.py`——`python scripts/bump_version.py 1.7.0` 从单一输入同步升级 `pyproject.toml` / `bot/__init__.py` / `web/package.json` 三处，任一文件缺失或版本格式非法即报错；`--check` 模式可在提交前校验三处是否一致，防止漏改（用法见 `CONTRIBUTING.md`）
- **LLM 子系统测试补强（消除覆盖黑洞）**：新增 `tests/test_llm_adapter.py`（26 个用例）、`tests/test_llm_manager.py`（25 个用例）、`tests/test_llm_mcp.py`（12 个用例）——`litellm_adapter.py` 覆盖 17%→93%、`llm/manager.py` 9%→69%、`llm/mcp.py` 0%→76%（mock `_get_litellm` / 假适配器与假 DB 隔离网络）；完整套件 358 passed，整体覆盖率 45.6%→51.9%
- **行尾规范化根治 CRLF 噪音**：新增 `.gitattributes`（源码/文档统一 `eol=lf`，`*.ps1`/`*.bat` 保留 `crlf`），`git add --renormalize` 后工作树 14 个 CRLF 假 `M` 全部消失，跨平台 diff 从此干净
- **Alembic 迁移修复与 CI 冒烟**：`alembic check` 检出 `messages.message_id` 索引与模型声明漂移（迁移建表时为非唯一，模型声明 `unique=True`），新增迁移 `eebc4752a3af` 改为唯一索引（旧库若存在重复 message_id 会在此步报错暴露）；新增 `scripts/verify_migrations.py`——隔离临时库上执行 `upgrade head` + 表完整性断言 + `alembic check`，已接入 CI quality job，防止模型改动后迁移脚本失效
- **覆盖率门槛上调**：全局 `--cov-fail-under` 40%→50%（当前 51.9%）；CI 新增 `bot/llm` 目录级门槛 70%（当前 76%），LLM 子系统覆盖率回退会被拦截

### Fixed
- **群配置「添加群」无反应**：`type=number` 输入框的 `v-model` 会把值转为数字类型，直接调用 `.trim()` 抛 `TypeError` 中断新增逻辑；改为先将输入 `String()` 转换为字符串再校验纯数字，现可正常添加群

## [1.6.0] - 2026-08-17

### Added
- **SDK git 依赖切换为 Gitee 镜像**：`pyproject.toml` 的 `qingci-plugin-sdk` git 依赖从 GitHub 主仓库切到 Gitee 镜像（`git+https://gitee.com/qingci-bot/Plugins-SDK.git@main`），国内源码安装 / Docker 构建克隆 SDK 更快更稳；本地 `build.ps1` 仍优先安装本地 SDK 源码
- **插件市场默认源改为 Gitee 镜像**：运行时拉取插件市场默认指向国内可及的 Gitee 镜像（`DEFAULT_MARKET_URL` / `MarketConfig.url` = `https://gitee.com/qingci-bot/Plugin-Market.git`），其为 GitHub 主仓库的自动同步只读镜像，国内拉取更快更稳；`market.url` 可切换至 GitHub 主仓库
- **代码托管迁移至 GitHub**：项目仓库自 AtomGit/GitCode 迁移至 GitHub 并统一文档与链接——`pyproject.toml` 的 SDK git 依赖、About 页托管信息与作者链接、README/CONTRIBUTING/CHANGELOG/ARCHITECTURE/PLUGIN_DEV 中所有 `atomgit.com` 链接统一改为 `github.com`；Plugin-Market 的 `index.json`（homepage/source）与 raw 索引地址分支同步（`master`→`main`）
- **CI 恢复为 GitHub Actions**：项目迁移托管至 GitHub 后，CI 重新落在 `.github/workflows/ci.yml`（GitHub Actions 原生），三个 job：`quality`（ruff lint + format + mypy + pytest + 覆盖率门槛）；`docker`（`docker compose config` 校验 + 镜像构建 + 容器运行冒烟，`/api/bot/health` 30s 就绪判定）；`install-script`（`bash -n` + `SKIP_SYS_DEPS=1 ./install.sh --dev` + `main.py` 启动冒烟，60s 超时）；`docker`/`install-script` job 使用预装 Docker 的 runner 提供运行时验证，替代此前 GitCode 平台 Kaniko 无 daemon 构建的限制
- **Linux/容器部署支持**：新增 `Dockerfile`（多阶段构建，`python:3.12-slim`，Headless 后端）与 `docker-compose.yml`（端口 8080/3001 映射 + `./instances` 卷持久化），`docker compose up -d` 一键拉起；新增 `install.sh`（Linux 一键安装脚本——自动检测 Python>=3.10、优先 uv 否则 pip、可选 `--vector`/`--with-gui`/`--dev`、系统依赖 autodetect apt/dnf/apk）；`.dockerignore` 排除 venv/产物/缓存，实例目录（可能含密钥）不进镜像；README 新增「2.1 Docker 容器部署」「2.2 Linux 源码部署」指南并说明 GUI 系统依赖与单实例降级
- **Web UI 视觉一致性大修**：全站消除 115 处内联样式乱象——① 卡片间距收敛为全局 `.page-body > .card + .card` 邻接规则，删除 16 处内联 `margin-top: 22px`；② toast 通知收敛为单一系统（useToast 改模块级单例，App.vue 统一渲染顶部悬浮通知，删除 6 处页面内 toast 渲染与 5 份重复过渡动画），页内常驻提示改用新 `.status-bar` 类；③ 开关控件全局化（`.switch`/`.slider` 收敛到 main.css，删除 Settings/PluginManager scoped 重复约 80 行，GroupConfig 原生 checkbox 统一为 switch）；④ 修复悬空类——全局新增 `.btn-accent`/`.btn-warning`/`.text-muted`/`.text-secondary`/`.tag-perm` 语义修正（PluginManager 禁用按钮改警告黄、更新按钮改 primary）；⑤ 清理旧主题残留（`--primary-color`/`--text-color`/`#6f8ffc` 统一为全局变量别名，MessageLog/ChatConfig 焦点色归位 `--blue`）；⑥ grid-4 断点统一（1100px→2 列、640px→1 列，删除 Dashboard scoped 覆盖）；⑦ Dashboard 卡中卡消除、统计字号统一、启停按钮统一 btn-sm；⑧ `/setup` 首次向导路由全屏化（App.vue 豁免侧边栏）；⑨ App.vue 侧边栏/面包屑内联样式类化；⑩ tab 控件视觉统一（PluginManager 分类/主 Tab、MessageLog 下划线式）；日志容器横向滚动修复
- **WebUI 平台配置表单**：系统设置页新增「平台适配器」卡片——Telegram 启用开关（switch）、Bot Token 输入（password + 显示/隐藏，保存时 `***` 占位符自动过滤保留原值）、轮询间隔数字输入；前端 `defaultConfig` 与表单模型加入 `platforms.telegram` 节，保存时随完整配置写回；后端 `token` 后缀命中既有敏感字段遮蔽/过滤逻辑，GET/PUT 零改动
- **WebUI 平台状态展示**：`GET /api/bot/status` 新增 `platforms` 数组（各适配器名称/展示名/连接状态/心跳时间/self_id，未启动时返回空列表）；WebUI 侧边栏状态区下方新增平台列表（状态点 + 展示名 + 在线/离线 + 心跳相对时间 tooltip）；store 新增 `platforms` 状态；TestBot `get_status` 与真实 Bot 对齐，`FakeConnection` 补全 PlatformAdapter 契约属性；测试 `test_platforms.py` 增 1 用例 + `test_api.py` 平台字段断言
- **多平台适配器**：新增 `bot/core/platforms/`（`base.py` PlatformAdapter 契约 + `telegram.py` Telegram Bot API 长轮询适配器）；事件归一化为 OneBot-11 兼容 dict（含 `platform` 字段），发送按 `MessageContext.platform` 路由到对应适配器；`OneBotConnection` 升级为实现契约的「onebot」平台（完全兼容）；配置 `platforms.telegram`（enabled/token/poll_interval），附加平台启动失败仅记日志不阻断主平台；SDK `MessageContext` 新增 `platform` 字段（v1.6.0）；测试 `test_platforms.py` 13 用例（归一化/发送映射/API 透传/配置解析/回复路由/dispatcher 透传）
- **插件市场体验打磨**：索引条目新增 `icon`（emoji 卡片图标）/`homepage`（主页链接）/`requirements`（依赖展示）/`tags`（标签筛选）字段；新增 `GET /api/plugins/market/info` 返回市场名称/插件数/索引更新时间（墙钟）；WebUI 市场 Tab 增强——市场名 + 索引更新时间、标签筛选栏、卡片图标、依赖标签、主页链接、加载失败重试按钮、已安装插件「卸载」入口；测试 `test_market.py` 增至 10 用例
- **类型化事件的 LLM 工具化**：新增 `bot/llm/events_tools.py`——`EventBuffer` 内存环形缓冲（默认 200 条）记录 notice/request 类型化事件，Dispatcher/TestBot 分发时自动入缓冲；`register_event_tools` 幂等注册两个只读 Function Calling 工具：`get_group_events`（按群查最近入群/退群/禁言/撤回/上传等事件）与 `get_member_events`（按成员查）；事件摘要按类型化字段提取（operator_id/duration/comment 等），结果格式化为 LLM 可读文本；仅记录仅查询，Bot 重启即清空；测试 `test_event_tools.py` 13 用例
- **插件市场（WebUI）**：新增 `bot/plugin/market.py`（MarketIndex/MarketClient/MarketManager）——集中索引（Gitee `qingci-bot/Plugin-Market`）拉取 + TTL 缓存 + 磁盘回退；列表合并已安装/可更新状态；安装/更新复用 `PluginManager.install`（卸载→覆盖重装）；WebUI 插件管理新增「插件市场」Tab（搜索/一键安装/更新/刷新）；配置 `market.url`/`market.refresh_interval`；`install()` 增强识别 `.git` 结尾 URL 为 git 仓库、`_locate_plugin_dir` 支持 `plugins/<name>/` 嵌套布局；测试 `test_market.py` 9 用例
- **类型化事件（notice/request）**：Dispatcher 在事件分发时将 notice/request 原始 dict 解析为类型化事件对象（SDK `events.py`，`bot/plugin/events.py` 转发），`MatcherContext.event` 持有，handler 按参数注解注入（如 `event: GroupIncreaseNotice`）；`resolve_handler_args` 新增事件类型注解注入规则；覆盖 9 种 notice 子类 + 2 种 request 子类，未知类型回退基类，数值安全转换，零依赖 dataclass 实现；测试 `test_typed_events.py` 10 用例
- **会话阶梯（多轮交互）**：Dispatcher 支持会话阶梯续接——handler 通过 `ctx.session`（SDK `Session`，`bot/plugin/session.py` 转发）调用 `pause()` 挂起等待同会话下一条消息续接同一 handler（跳过命令前缀规则）、`finish()` 结束、`reject()` 拒绝继续等；Session 实例跨轮复用保留自定义状态；阶梯默认 300s 超时自动失效，插件卸载/禁用时自动清理；测试 `test_session_steps.py` 7 用例覆盖 pause/reject/finish/隔离/超时/清理
- 支持基于独立插件 SDK（`qingci_plugin_sdk`）编写的外部插件：`PluginManager` 现可识别并注册 SDK 式 `PluginBase` 子类（此前仅识别 `bot.plugin.base.PluginBase`），加载时自动将 SDK 插件数据目录重定向到当前实例可写数据根（`data_root()/plugins/<name>/`），保持实例隔离
- 打包：`qingci-bot-ce.spec` 通过 `collect_all('qingci_plugin_sdk')` 将独立插件 SDK 整体打入 exe，外部插件运行时 `import qingci_plugin_sdk` 不再 `ModuleNotFoundError`；`build.ps1` 在打包前显式安装 `Plugins-SDK`（相对路径依赖在 `pyproject.toml` 中无法解析，故在构建脚本中安装源码包）
- 测试：新增 `sdk_plugin` 用例，验证 SDK 式插件可被管理器加载、`data_dir` 重定向到 bot 数据根
- 插件依赖管理：目录型外部插件加载前自动把 `requirements.txt` 声明的第三方依赖安装到实例隔离目录（`data_root()/deps/`）并注入 `sys.path`，插件可 `import` 其专属依赖且不污染主程序环境；`bot.auto_install_plugin_deps` 可关闭以满足供给链安全（默认开启）
- 打包：`qingci-bot-ce.spec` 内嵌 `pip`（`collect_all('pip')`），打包模式下自动安装插件依赖到实例 `deps` 目录；源码环境优先 `uv pip install --target`，缺失时回退嵌入 pip
- 性能优化：`BotConfig.admin_set` 预编译集合（`super_admin` + `admin_users` 并集，O(1) 成员判断），权限判定由 O(n) 列表遍历降为 O(1)；`rule` 限流豁免与敏感词豁免同步受益
- 性能优化：`PluginManager.all_matchers(post_type)` 事件类型倒排索引，事件分发按类型直接取 Matcher，不再对全部 Matcher 线性扫描过滤
- 测试：新增 39 个用例（告警、限流、API 实例路由、登录路由、`session_scope`、RAG 增量索引等），全套件 193 个全部通过
- 引入 GitHub Actions CI（`.github/workflows/ci.yml`）：ruff lint + format + mypy + pytest + 覆盖率门槛

### Changed
- 架构：协议层（`PluginBase`/`Matcher`/`Permission`/`Rule`/`MessageContext`）统一由独立插件 SDK 维护，`bot/plugin/{base,matcher,permission,rule,ratelimit}.py` 与 `bot/core/dispatcher.py` 的 `MessageContext` 改为薄转发（`from qingci_plugin_sdk.* import *`），消除两处定义漂移（净删约 500 行重复代码）；SDK 由可选升级为主项目正式依赖（git 依赖声明于 `pyproject.toml`，构建/本地开发仍走 `build.ps1` 的 `-e` 安装）
- 架构：`QingciBot.__init__` 的组件装配（核心服务创建 + DI 注册）抽离到组合根 `bot/core/composition.py` 的 `assemble_bot()`，`__init__` 只保留配置加载与状态字段；新增 `build_bot()` 便捷入口
- 架构：全局单例 `get_bot()` 不再持有 bot 实例，改为持有 DI 容器引用、从容器解析（`resolve_sync(QingciBot)`），消除模块级 bot 状态与多实例的潜在冲突
- API：`DIContainer` 新增公开 `resolve_sync()`（同步解析，供非异步上下文）
- 弃用：`PluginBase` 旧式回调 `on_message`/`on_notice`/`on_request` 标注 deprecated，新插件请改用 Matcher（内置插件均已迁移）
- 打包：`litellm` 的 `proxy/_experimental/out`（Next.js Web 前端静态产物，约 22MB）从 datas 中过滤，exe 体积下降
- 依赖：`lancedb` 由主依赖移至 optional 的 `vector` 分组；`KnowledgeStore` 在 `lancedb` 缺失时（vector 模式）自动回退 keyword 后端并告警
- 性能优化：`bot/db` 新增 `session_scope()` 上下文管理器统一 commit/rollback/close，Database 仓储全部方法改用，减少重复样板
- 性能优化：RAG 关键词库 `add_document`/`remove_document` 改为增量索引，仅更新受影响文档，不再全量重建
- 覆盖率防回退门槛：pytest 新增 `--cov-fail-under=40`，低于 40% 直接失败
- WebSocket 鉴权：API Key 由 URL 查询参数改为子协议（`sec-websocket-protocol: api-key.<token>`）传递，避免敏感信息落入访问/代理日志；query 参数保留为兼容回退
- 前端启停 Bot 失败时通过 toast 给出明确错误提示（此前 Promise rejection 被静默吞掉）

### Fixed
- 修复 18 个既有 mypy 类型错误（内建插件 `self.bot`/`self.config`/`self.llm` 为 Optional 的 union-attr），方法入口加 `assert` 类型收缩，全量 mypy 通过
- 配置模板 `config.example.yaml` 补充 `bot.wizard_skipped` 与 `log` 节（`level`/`log_file_enabled`/`log_file_max_bytes`/`log_file_backup_count`/`log_dir`）说明

### Docs
- 全面重构文档以匹配新架构：`ARCHITECTURE.md` 新增「协议层归属」「组合根」「DI 解析单例」章节与依赖说明；`docs/PROJECT_STRUCTURE.md` 标注薄转发目录与协议层依赖方向；`docs/CODING_STANDARDS.md` 新增「协议层归属」「装配与单例」约束；`README.md` 补充 SDK 正式依赖与 `[vector]` 分组说明；`PLUGIN_DEV.md` 更新两种插件形态（基类同一来源）、示例五改为 Matcher 优先并标注旧式回调 deprecated、PEP 604 示例；`CONTRIBUTING.md` 补充 SDK 安装与协议层修改指引；插件模板同步标注旧式回调 deprecated
- `PLUGIN_DEV.md` 修正实例目录功能版本描述（「自 v1.6 起」→「自 v1.5.1 起」），补充子命令/类型化参数等示例

## [1.5.1] - 2026-08-16

### Added
- 实例管理：侧边栏新增实例列表，支持新建/删除/切换/重命名实例
- 实例=完全自包含目录（`instances/<name>/`，含 `config.yaml` + `plugins/` + `data/`），新增 `--instance <name>` 一次性决定 config/data_root/plugins/port 四个维度，可整体复制/迁移/备份
- 新增实例管理 API：`GET/POST/DELETE /api/instances`、`PUT /api/instances/{name}`（重命名目录 + 更新元数据）、`POST /api/instances/{name}/start`（重启进程到目标实例）
- 运行中实例也支持重命名：改名后自动重启到新名称（复用切换实例的 relaunch 机制），避免 Windows 文件锁阻止目录改名
- `bot.paths` 新增 `plugins_dir()`/`set_plugins_dir()`：外部插件代码目录默认 `app_root()/plugins`，实例模式下指向实例内 `plugins/`
- 命令权限等级显示：`Permission` 新增 `label` 属性，内置权限标注可读标签，组合（`&`/`|`/`~`）自动生成组合标签；`describe_permission()` 返回可读标签；命令管理接口追加 `permission` 字段，Web「命令管理」表格新增权限列并映射为中文（超级管理员/管理员/所有人等）
- `on_message` 新增 `description` 参数（与 `on_command`/`on_startswith`/`on_keyword` 对齐），存入 `meta.description` 供 `/help` 与命令管理展示

### Changed
- 移除全局模式：启动必须绑定一个实例（无实例时自动创建 `default`，未指定 `--instance` 时自动启动到默认实例）
- 外部插件目录、在线安装、热重载、插件元数据发现统一改用 `plugins_dir()`，不再硬编码 `app_root()/plugins`
- 端口自动分配从 8080 起：首个实例占用 8080，后续实例依次递增
- 构建脚本 `build.ps1` 不再生成根级 `config.yaml`/`data\`：配置/插件/数据均在实例自包含目录内，产物随实例目录分发
- 优化实例管理区块样式：改用全局 CSS 变量统一配色，增强「新建/重命名/删除/切换」按钮对比度与交互（hover 高亮、当前实例琥珀高亮）

### Fixed
- 修复"切换实例/重启"实际从未生效：旧实现用 `os._exit` 终止进程，会一并杀死等待重启的后台线程，导致新进程从未被拉起。改为派发独立分离的助手进程（`desktop/relaunch.py`）等待旧进程退出后再拉起目标实例
- 修复根级 `config.yaml` 残留/兜底生成：API 鉴权与配置接口在未显式指定 `--config` 时改用 `bot.instances.default_config_path()`（默认实例的 `config.yaml`），不再回退到 `app_root()/config.yaml`，保证配置始终落在实例自包含目录内

## [1.5.0] - 2026-08-16

### Added
- 管理员细分为**超级管理员**（唯一，`bot.super_admin`）与**普通管理员**（多个，`bot.admin_users`）：`SUPERUSER` 权限仅超级可命；`ADMIN` 权限普通管理员可命，超级管理员自动继承
- 多实例支持：新增 `--data-dir` 参数指定可写数据根目录（DB/日志/插件数据等），在同一台机器上可运行多个相互隔离的实例
- 单实例保护升级：互斥名由数据根目录派生，同一实例（同数据目录）重复双击聚焦已有窗口，不同实例（不同 `--data-dir`）互不阻塞可多开

### Changed
- 内置命令权限映射：`/status`、`/clear` 降级为普通管理员（`ADMIN`）；`/blacklist`、`/filter`、`/group`、`/kb` 保持超级管理员（`SUPERUSER`）
- 限流豁免、聊天敏感词 `exempt_admins` 豁免、错误告警通知目标均同时纳入超级管理员
- 数据库、日志、备份、插件数据等所有可写路径统一改为基于 `data_root()` 解析，尊重 `--data-dir` 设置

### Fixed
- 修复双击 exe / 重复启动会新建多个界面与进程：在入口加入单实例保护（Windows 命名互斥量），重复启动时聚焦已有窗口并退出，避免多窗口与端口冲突

## [1.4.1] - 2026-08-14

### Fixed
- 修复内置插件（`bot/plugin/builtin/*`）从单文件迁移到目录结构后相对导入层级错误（`from ..base` → `from ...base` 等），导致内置插件在源码与打包产物中均无法加载
- `load_builtin` 增加显式内置插件清单回退：PyInstaller 打包后 `pkgutil.iter_modules` 无法扫描 PYZ 归档内模块，扫描落空时按 `_BUILTIN_PLUGINS` 清单加载（与 `qingci-bot-ce.spec` 的 hiddenimports 保持一致）

### Docs
- 同步 `ARCHITECTURE.md`、`docs/PROJECT_STRUCTURE.md` 的目录树与当前实现：修正 PyInstaller spec 文件名（`qingci-bot-ce.spec`）、`bot/core/` 补 `event_bus.py`、`bot/plugin/` 补 `ratelimit.py`/`llm_tool.py`/`watcher.py`、`builtin/` 与 `plugins/` 由单文件改为目录结构；`PLUGIN_DEV.md` 修正 spec 文件名引用

## [1.4.0] - 2026-08-14

### Added
- 参数级依赖注入：Matcher handler 参数按签名自动解析注入（`MatcherContext`、`Bot`、DI 服务），支持 `Depends(...)` 显式声明与类型注解自动注入
- 全局生命周期钩子：插件可覆写 `on_startup` / `on_shutdown` / `on_bot_connect` / `on_metaevent`，在 Bot 启动/停止、LLBot 连接建立、元事件到达时获得通知（异常隔离）
- 插件数据目录：`PluginBase.data_dir` 属性，提供插件专属数据目录 `data/plugins/<name>/`（自动创建，卸载不删除）
- 在线插件安装：`PluginManager.install(bot, source)` 支持从 git 仓库、HTTP 归档 URL、本地目录/归档安装插件到 `plugins/` 并自动安装 `requirements.txt`（或 `plugin.json` 的 `requirements` 字段）声明的依赖
- 国际化（i18n）：`I18n` 翻译器 + 插件 `i18n/<locale>.json` 翻译资源自动加载，`self.i18n` / `self._` 使用；新增 `config.yaml` 的 `lang` 字段控制全局语言（默认 `zh-CN`）
- 事件总线：`EventBus` 跨插件发布-订阅事件广播，插件无需显式依赖即可协作；支持 `subscribe`/`publish`、通配订阅 `"*"`、sync/async handler、线程安全；注入到 `PluginBase.event_bus` 与 DI 容器
- 插件级 LLM 工具声明：`@llm_tool` 装饰器让插件注册 Function Calling 工具，参与 LLM 推理；工具名自动加插件名前缀（`<plugin>_<name>`），卸载时自动注销
- 指令系统增强：`on_command` 新增 `aliases`（命令别名）、`subcommands`（子指令路由）、`args_schema`（类型化参数解析并按名注入 handler 形参）
- 配置 schema 自动生成：插件定义 `Config` 内嵌类（pydantic）自动导出 JSON Schema，Web 插件管理页据此渲染配置表单，无需手写 UI；新增 `GET/PUT /api/plugin/{name}/config` 接口
- 自动热重载：`PluginWatcher` 监听外部插件目录文件变更并自动重载插件（开发期提效）；由 `config.yaml` 的 `hot_reload.enabled` / `hot_reload.interval` 控制，默认关闭
- 细粒度事件处理钩子：新增 Matcher 运行前全局钩子（`run_preprocessor`，`bot.add_matcher_preprocessor`，在 Matcher 匹配成功后、handler 前触发，返回非 None 即拦截该 Matcher）与平台接口调用钩子（`on_calling_api`，`bot.register_api_hook` / `connection.on_api_call`，每次 OneBot API 调用前触发，可改写参数或抛异常阻止调用）

### Fixed
- 修复 `DELETE /api/log/sessions/one` 接口（`delete_session`）使用 `Request | None` 参数导致 FastAPI 启动时路由注册失败、API 无法启动的问题

### Changed
- mypy 配置 `python_version` 由 3.10 提升至 3.12，与推荐运行版本保持一致
- 代码质量维护：修复 ruff 检查 318 处、mypy 类型检查 82 处错误，并对 58 个文件统一格式化；`bot/db/database.py` 针对 SQLModel 列访问按文件禁用相关 mypy 错误码

## [1.3.0] - 2026-08-13

### Added
- 插件 Web 管理页面：`register_page(title, icon, static_dir)` 方法，插件可在 `on_load` 中注册管理页面入口；框架自动挂载插件静态文件到 `/api/plugin-data/{name}/`；前端插件管理页展示「管理」按钮，点击后右侧抽屉 iframe 加载
- 插件目录结构：`load_external_dir()` 支持目录型插件（`plugins/<name>/__init__.py`），可含 `web/` 子目录和 `plugin.json`；同名时目录型优先于文件型
- 命令管理：`Matcher.disabled` 字段支持禁用单个命令；`GET /api/command/conflicts` 列出所有命令并标记冲突；`PUT /api/command/{owner}/{command}` 支持禁用/启用/调整优先级；前端插件管理页新增「命令管理」Tab，冲突行红色高亮

### Changed
- 内置插件全部转为目录结构：`admin/`、`chat/`、`help/`、`imagegen/`、`knowledge/`
- 外部插件示例和模板转为目录结构：`hello/`、`_template/`
- 插件 API 列表接口追加 `pages` 字段

### Fixed
- 修复 `check_availability` 在 litellm 导入失败时引用未绑定局部变量导致 `NameError` 二次崩溃
- 修复中间件拦截 Matcher 时指标重复计数

## [1.2.1] - 2026-08-11

### Fixed
- 修复 `request` Matcher 审批结果（True/False）被丢弃，导致加好友/加群审批永不执行
- 修复 `chat()` 被取消时用户消息不回滚，导致内存与数据库残留孤立消息
- 修复连接监控回调 `awaitable` 判断使用 `asyncio.iscoroutine()` 无法识别 Future/Task，改用 `inspect.isawaitable()`

## [1.2.0] - 2026-08-10

### Added
- 插件临时禁用/启用：`enabled` 字段 + `on_disable`/`on_enable` 钩子 + API 端点 + 前端开关
- 插件状态管理：`PluginStatus` 枚举（LOADING/LOADED/DISABLED/ERROR/UNLOADING）替代原布尔值
- 插件级配置：`config.yaml` 中 `plugins.<name>` 节，插件可定义 `Config` 内嵌类自动校验
- 插件导出/导入机制：`export()` / `require()` 方法，支持插件间服务接口暴露
- 插件级中间件：`register_before()` / `register_after()` 钩子，可拦截/修改 handler 返回值
- 插件分类：`category` 字段 + 前端分类标签页筛选
- 执行指标监控：Matcher 调用次数、平均耗时、错误率 + API `/api/plugin/{name}/metrics` + 前端指标面板
- 插件元数据发现：`plugin.json` 文件 + API `/api/plugin/discover/metadata`，无需导入模块即可发现插件信息
- 帮助命令输出增强：按插件分类分组展示，支持权限过滤
- 依赖版本约束：`require` 支持 PEP 440 版本规范（如 `"chat>=1.0,<2.0"`）
- Plugins-Dev SDK 同步：新增 `PluginStatus`、`category`、`export`/`require`、中间件、`plugin_config` 等字段
- 会话状态管理：`SessionState` + `SessionStateManager`，TTL 键值存储，`ctx.session_state` 便捷访问
- 会话状态优化：`asyncio.Lock` 并发安全、`pop`/`expire`/`ttl`/`items` 便捷方法、`remove_session` 显式删除、`stats` 统计、`serialize`/`deserialize` 持久化、会话数上限保护
- 依赖注入容器：`DIContainer` 按类型自动注入，支持 SINGLETON/TRANSIENT/SCOPED 生命周期
- DI 容器优化：`asyncio.Lock` 并发安全、`register_as` 接口绑定、`Optional[X]` 类型提取、`inject` 不覆盖已赋值属性、`register_sync`/`inject_sync` 同步兼容方法
- 插件测试工具：`bot.testing` 包（TestBot 轻量测试环境 + 事件构造器），插件作者可用 pytest 模拟消息事件、断言回复与主动发送

### Fixed
- 修复 `PluginBase.require` 属性与同名方法冲突：方法改为 `get_exports()`
- 修复 `SessionStateManager.get_session` 清理时误删刚创建的会话导致 `KeyError`
- 修复 `Matcher` 不可哈希导致指标记录 `TypeError`：改为身份比较 dataclass
- 修复 `MatcherContext` 缺 `Any` 导入
- 修复首次启动向导（Setup Wizard）无鉴权可篡改配置：已配置 `api_key`/`admin_users` 后禁止重复引导
- 修复插件元数据发现接口导入路径错误导致 500
- 敏感字段脱敏增强：改为后缀匹配，覆盖 `embedding_api_key` 等字段
- 修复启动画面（splash）关闭后窗口无法销毁
- 修复 RAG 向量库同步方法阻塞事件循环：新增 `*_async` 异步方法并更新调用方
- 修复配置损坏时静默覆盖原文件：损坏文件先备份为 `.bak` 再重建默认配置
- 修复一次性（temp）Matcher 在 handler 异常后残留：移除操作移入 `finally`
- `Message.append` 支持展开列表/元组参数，非法类型抛 `TypeError`
- 修复 `LLMManager.clear_session` 内存与 DB 清除竞态：DB 清除移入会话锁内
- 修复 MCP 工具注册失败时桥接器资源泄漏：未注册 bridge 在 `finally` 中关闭
- 修复 `LLMManager.reload` 锁快照竞态：新增全局重载锁，防新建会话逃过清空
- 修复 API 删除会话绕过 LLMManager 导致内存历史"复活"：新增 `clear_session_by_key` 统一清理
- 修复会话历史查询无 id 兜底排序导致的乱序
- 修复 `GROUP_MEMBER` 权限语义：仅群聊消息生效，私聊一律不匹配
- 修复 `is_connected` 仅检测 API 通道：事件通道连接也视为已连接
- 修复对话调试 WebSocket 收到非法 `user_id` 导致连接异常断开

## [1.1.0] - 2026-08-10

### Added
- 依赖分组：`[test]` / `[build]` / `[dev]` 三级拆分，按需安装
- 代码质量工具：ruff（代码风格）、mypy（类型检查）、pre-commit hooks
- 测试用例：API 端点、配置管理、数据库操作（pytest + pytest-cov）
- 文档：CHANGELOG.md、CONTRIBUTING.md、SECURITY.md
- 前端工程化：ESLint + Prettier 配置
- 全局 API 异常处理器（统一错误响应格式）
- 日志轮转：按文件大小轮转，保留最近 N 个备份（`log.log_file_enabled` 等配置）
- 模块级 Logger（`logging.getLogger("qingci-bot.xxx")`）

### Changed
- README 大幅更新：依赖表格、测试/代码质量章节、文档链接集中管理
- 开发依赖安装命令从 `pip` 改为 `uv pip`，虚拟环境创建统一用 `uv venv`

### Fixed
- 启动窗口期事件丢失：`_running` 标志提前到 `connection.start()` 后立即设置
- `stop()` 部分清理遗漏：新增 `_started` 标志，部分启动失败时仍正常清理资源
- `LLMManager.reload()` 锁获取中断风险：用 `acquired` 列表追踪已获取锁
- `chat_with_tools()` 空指针防御：`registry` 为 None 时跳过工具执行

## [1.0.0] - 2026-08-10

### Added
- 完整的 QQ Bot 框架，对接 LLBot（OneBot 11 协议）
- 多 LLM 提供商支持：OpenAI / DeepSeek / Ollama / SiliconFlow / Claude / Gemini / 自定义
- 插件系统：借鉴 NoneBot2 的 Matcher / Rule / Permission 设计，支持热加载
- Web 管理端：Vue 3 + 原神风格暗色主题
- 桌面应用：PyWebView 套壳 + 系统托盘 + 开机自启 + 启动加载画面
- 轻量知识库：关键词 + LanceDB 向量检索双模式
- 会话摘要：历史超长时自动压缩，保留最近 N 轮原文
- Function Calling：支持多轮工具调用（含 MCP 服务器接入）
- 定时任务调度器（基于 APScheduler）
- 错误告警：ERROR 日志达到阈值时私聊通知管理员
- 敏感词过滤
- 群粒度配置（启用/禁用 + 触发模式）
- 人格切换（/persona 命令）
- LLM 用量统计
- 审计日志
- 数据备份与恢复
- 流式对话（WebSocket + SSE）
- 完全离线运行（无 CDN 依赖）
- 一键打包为 Windows EXE（PyInstaller）

[1.7.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.7.0
[1.6.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.6.0
[1.5.1]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.5.1
[1.5.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.5.0
[1.4.1]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.4.1
[1.4.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.4.0
[1.3.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.3.0
[1.2.1]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.2.1
[1.2.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.2.0
[1.1.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.1.0
[1.0.0]: https://github.com/Qingci-Bot/Qingci-Bot-CE/releases/tag/v1.0.0