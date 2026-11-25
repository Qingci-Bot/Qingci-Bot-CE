# Qingci-Bot CE

> **代码托管**：本项目以 [GitHub](https://github.com/Qingci-Bot/Qingci-Bot-CE) 为唯一仓库；贡献与提 PR 一律以 GitHub 为准。

> **版权与授权**：本项目（含 [Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK)）原为 **[Zhou Zhe (aka luoqingci)](https://github.com/luoqingciya)** 的个人项目，版权完整、排他地归其个人所有；[Qingci-Bot](https://github.com/Qingci-Bot) 组织获永久、不可撤销的 GPLv3 使用 / 修改 / 分发许可及维权授权，负责持续维护，**不是共同版权人**。完整授权文本见 [NOTICE](./NOTICE)。

> **合规声明**：本项目不含任何对 QQ / 腾讯客户端或通信协议的逆向工程、破解或模拟登录，仅通过公开的 OneBot 11/12 标准接口、QQ 开放平台官方机器人接口与 Telegram Bot API 接入，详见 [合规声明](#合规声明)。

基于 Python 的多平台机器人框架，内部统一采用 **OneBot 12 事件模型**（`type` / `detail_type` / `message[]` 消息段），基于 [aiocqhttp](https://github.com/nonebot/aiocqhttp) 对接任意 OneBot 11 反向 WebSocket 协议端（如 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot) / NapCat / go-cqhttp，作为兼容输入层自动翻译为 v12 事件），支持 LLM 智能对话、Web UI 和桌面应用。适配器将各平台归一化为 OneBot 12 内部模型，插件对来源平台完全无感知。

> 独立插件开发：[Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK) — 零依赖插件开发 SDK，无需克隆主项目即可开发插件
>
> 插件协议层（`PluginBase`/`Matcher`/`Permission`/`Rule`/`MessageContext`）统一由 Plugins-SDK 维护，主项目 `bot/plugin/` 下为薄转发，内置插件与外部插件共用同一套 API
>
> 系统架构、项目结构、技术栈详见 [ARCHITECTURE.md](./ARCHITECTURE.md)

## 特性

- **OneBot 12 内核**：内部统一采用 OneBot 12 事件模型（`type` / `detail_type` / 标准 `{type,data}` 消息段，媒体以 `file_id` 引用）；基于 [aiocqhttp](https://github.com/nonebot/aiocqhttp) 对接任意 OneBot 11 反向 WebSocket 协议端（如 LLBot / NapCat / go-cqhttp），v11 事件在入口由 `v11_compat` 翻译层自动归一化为 v12 事件，并对存量插件保留 v11 兼容字段（`post_type` / `message_type` / `raw_message`）——OneBot 11 只是"众多平台之一"

- **多平台适配器**：平台协议归一化为 OneBot 12 内部事件模型（`PlatformAdapter` 契约），插件对平台无感知；内置 OneBot 11（v11 反向 WS + `v11_compat` 翻译）+ **OneBot 12（原生反向 WS，事件直通无需翻译，动作 JSON-RPC；扩展通知如红包运气王/荣誉变更/名片变更/精华消息/群签到/好友戳一戳已类型化，插件可** **`on_notice`** **+ 类型注解消费）** + Telegram（Bot API 长轮询，`platforms.telegram` 配置启用）+ **QQ 官方机器人（QQ 开放平台 Bot API v2，`platforms.qq` 配置启用）**，回复按事件来源平台自动路由；Telegram 适配器支持群聊 `@Bot` 提及触发（at 触发模式）、图片/语音/视频收发（收到 photo → `image`、voice → `voice`、video → `video` v12 段；发送将 v12 `image`/`voice`/`video` 段映射到 `sendPhoto`/`sendVoice`/`sendVideo`）、回复段与成员变动通知（成员进出群/权限变更归一化为 `group_member_increase`/`group_member_decrease`/`group_admin_*` notice）；长轮询采用有限并发消费（慢更新不阻塞同批）且失败更新自动确认跳过避免重放，连接失败指数退避并自动重连，Bot Token 支持运行时热更新（自动重验身份），HTTP 超时/重试可配置，API 调用错误按 401/403/404 分类，平台状态接口暴露连接健康指标（连续错误数/最近错误与断连时间/退避状态）

- **QQ 官方机器人接入**：走 QQ 开放平台公开的机器人接口（AppID + AppSecret 鉴权，WebSocket 网关 + OpenAPI v2），非协议端方案；群聊 `@机器人` 与消息列表单聊事件归一化为 v12 消息事件，附件按 `content_type` 映射为 `image`/`voice`/`video`/`file` 段；支持文本与富媒体回复（图片/视频/语音/文件先上传再发送）、`Resume` 断连补发与会话恢复、token 自动刷新、入站消息按 `d.id` 去重、平台错误按「鉴权 / 权限 / 被动回复窗口 / 判重」分类；<strong>受平台限制</strong>：开放平台没有好友 / 群列表接口，且被动回复有时效与条数上限（群聊 5 分钟 / 5 条，单聊 60 分钟 / 4 条），超出后需用户再次发消息

- **LLM 统一接口**：基于 [litellm](https://github.com/BerriAI/litellm)，支持 7 大提供商（OpenAI / DeepSeek / Ollama / SiliconFlow / Claude / Gemini / 自定义），含流式响应、Function Calling、多模态；填好 API Key 后可一键拉取提供商可用模型列表

- **内置「大肥鱼」人设**：内置聊天插件即「大肥鱼」，出厂自带一套温和爱开玩笑的 Q 版胖鱼人设；另可配置多组自定义人设（system\_prompt 集合），聊天中 `/persona` 命令随时切换（会话级覆盖），Web UI 可视化管理

- **会话上下文管理**：按群聊/用户独立维护对话历史，内存 + 数据库双写持久化，按条数与 Token 双重裁剪（可选摘要压缩）；Web UI 按会话分组可视化查看 / 删除

- **插件系统**：借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持命令/前缀/关键词/正则/通知/请求匹配，优先级调度、权限控制、插件间依赖声明（require + PEP 440 版本约束）、插件级配置（config.yaml 节）、插件间导出/导入（export/require）、插件级中间件（before/after handler）、handler 参数级依赖注入（Depends）、全局生命周期钩子（on\_startup/on\_shutdown/on\_bot\_connect/on\_metaevent）、跨插件事件总线（EventBus 发布-订阅）、插件级 LLM 工具声明（`@llm_tool` 参与 Function Calling）、指令系统增强（别名 / 子指令 / 类型化参数）、插件数据目录（data\_dir）、国际化（i18n）、在线插件安装与依赖自动安装、配置 schema 自动生成 Web 配置表单、开发期自动热重载、细粒度事件处理钩子（run\_preprocessor Matcher 运行前钩子 + on\_calling\_api 平台接口调用钩子）、插件状态管理（PluginStatus 枚举）、执行指标监控、元数据发现（plugin.json）；支持加载/卸载/重载/禁用/启用，禁用时保留实例并跳过事件分发；卸载默认仅删代码目录、保留数据与依赖（便于重装），插件管理页可「彻底删除（purge）」连数据目录与第三方依赖一并清除。协议层（PluginBase/Matcher/Rule/Permission/MessageContext）由独立插件 SDK 单一维护，内置插件与外部插件行为一致

- **安全与运维**：API Key 鉴权（登录防暴力限流）、敏感词过滤、对话限流、登录审计、数据库在线备份、错误告警、结构化 JSON 日志（可选）、Prometheus 指标导出（`/api/metrics`，含事件积压/丢弃、消息缓冲、插件与 Matcher 调用指标）

- **增强能力**：AI 图片生成、轻量知识库（关键词检索零依赖；向量检索需可选依赖 lancedb）、会话摘要（历史裁剪）、Function Calling（内置时间/一言/群事件查询工具）、MCP 服务器接入、定时任务调度器、LLM 用量统计

- **HTML 渲染服务**：基于 Playwright 无头 Chromium 将 HTML 渲染为 JPEG/PNG（可选依赖 `[render]`），供签到卡等「HTML 模板 → 图片消息」插件复用；playwright 未安装/浏览器缺失时自动降级不可用，不影响框架启动

- **数据库 ORM**：SQLModel 模型定义 + Alembic 迁移管理，异步会话（aiosqlite + WAL 模式），支持在线备份与消息 CSV 导出

- **Web UI**：原神风格暗色主题，登录页 / 仪表盘（用量图表）/ LLM 配置（提供商联动 + 模型列表 + 人设 + MCP 管理）/ 对话调试台（流式聊天测试）/ 群配置 / 插件管理（分类筛选 + 状态管理 + 指标面板 + 卸载/彻底删除 + 插件市场一键安装/更新/搜索）/ 命令管理（冲突标记 + 禁用/优先级调整 + 权限等级显示）/ 消息日志（消息流 + 会话记录）/ 运行日志（实时日志流 + 级别过滤，受 `log.run_log_enabled` 开关控制）/ 登录审计 / 系统设置。独立「实例管理」页面支持新建/删除/切换/重命名实例（含端口、启用的适配器、数据占用等信息）

- **桌面应用**：Electron + Python 混合架构 — Electron 桌面壳 spawn Python 后端（`main.py --backend`）并加载其 Web UI；系统托盘（关闭窗口自动驻留后台）、启动画面、单实例（按 data-dir 退化的控制端口）均由壳承担；`build-electron.bat` 产出安装版 Setup.exe + 绿色解压 zip 一键分发

- **离线可用**：前端资源本地打包，无外部 CDN 依赖；litellm 延迟导入，启动不加载重型依赖

***

# 快速开始

```bash
git clone https://github.com/Qingci-Bot/Qingci-Bot-CE.git
cd Qingci-Bot-CE
uv sync --locked
uv run python main.py --host 127.0.0.1
```

打开 <http://127.0.0.1:8080> 进入 Web UI。首次启动会在实例目录生成默认 `config.yaml`，
按 [配置参考](./docs/CONFIGURATION.md) 填好协议端与 LLM 后即可收发消息。

> 需 Python 3.11+；Web UI 已预构建（`web/dist`），常规运行无需 Node.js。
> 完整环境要求、Docker / Linux 一键部署、桌面 EXE、命令行参数与管理命令见
> [docs/SETUP.md](./docs/SETUP.md)；运行测试与代码质量命令见
> [CONTRIBUTING.md](./CONTRIBUTING.md)。

> 插件开发、API 接口、前端开发与打包详见 [PLUGIN_DEV.md](./PLUGIN_DEV.md)；独立插件开发
> SDK：[Plugins-SDK](https://github.com/Qingci-Bot/Plugins-SDK) — 零依赖插件开发工具包。

## 下载与分发形态

每个版本发布到 [GitHub Releases](https://github.com/Qingci-Bot/Qingci-Bot-CE/releases)，产物命名统一为「包名 + 版本 + 架构」，各格式沿用其生态的分隔符约定：

| 场景 | 文件 | 说明 |
| --- | --- | --- |
| Windows 桌面（推荐） | `qingci-bot-ce-<ver>-x64-setup.exe` | NSIS 安装向导，可选安装目录与数据目录 |
| Windows 桌面（免安装） | `qingci-bot-ce-<ver>-x64-portable.zip` | 解压即用，解压后运行 `Qingci-Bot CE.exe` |
| Debian / Ubuntu 桌面 | `qingci-bot-ce_<ver>_amd64.deb` | 应用菜单启动，数据在 `~/.local/share/Qingci-Bot CE` |
| Fedora / RHEL 桌面 | `qingci-bot-ce-<ver>-1.x86_64.rpm` | 同上 |
| Debian / Ubuntu 服务端 | `qingci-bot-ce-server_<ver>_amd64.deb` | headless + systemd，装完 `systemctl start qingci-bot-ce` |
| Fedora / RHEL 服务端 | `qingci-bot-ce-server-<ver>-1.x86_64.rpm` | 同上 |
| Debian / Ubuntu 桌面（ARM64） | `qingci-bot-ce_<ver>_arm64.deb` | 树莓派 4/5、ARM 云主机、Apple Silicon 虚拟机等 arm64 桌面 |
| Fedora / RHEL 桌面（ARM64） | `qingci-bot-ce-<ver>-1.aarch64.rpm` | 同上 |
| Debian / Ubuntu 服务端（ARM64） | `qingci-bot-ce-server_<ver>_arm64.deb` | arm64 服务器，headless + systemd |
| Fedora / RHEL 服务端（ARM64） | `qingci-bot-ce-server-<ver>-1.aarch64.rpm` | 同上 |
| 校验和 | `SHA256SUMS.txt` | `sha256sum -c SHA256SUMS.txt` 校验上述 10 个包（含 arm64） |

Linux 不提供 AppImage：桌面与服务端都以发行版原生包分发，依赖、升级与卸载交给包管理器。

> **发行版要求**：Linux 包自带 Python 运行时，要求 **glibc ≥ 2.35**（Ubuntu 22.04+ / Debian 12+ / Fedora 36+）。早期 1.22.0 在 24.04 上构建，导致 22.04 / Debian 12 启动报 `GLIBC_2.38' not found`，1.22.1 已修复；详见 [docs/PACKAGING.md](./docs/PACKAGING.md#构建机的-glibc-决定运行时下限)。

安装步骤见 [docs/SETUP.md](./docs/SETUP.md#linux-发行版包deb--rpm)，命名与校验规则见 [docs/PACKAGING.md](./docs/PACKAGING.md#产物命名与校验)。

Docker 镜像同版本发布到 `ghcr.io/qingci-bot/qingci-bot-ce`（tag：`X.Y.Z` / `X.Y` / `X` / `latest`，`docker/metadata-action` 会剥掉 `v` 前缀）。
镜像为 **amd64 + arm64 多架构 manifest**（1.22.4 起），ARM 主机上 `docker pull` 会自动拿到 arm64 层。

***

## 文档

| 分类 | 文档 | 内容 |
| --- | --- | --- |
| 上手 | [docs/SETUP.md](./docs/SETUP.md) | 环境要求、安装、本地 / Docker / Linux 部署、桌面 EXE、命令行参数、管理命令、API 鉴权与监控 |
| 上手 | [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md) | 故障排查：协议端、鉴权、能力降级、性能、Docker |
| 配置 | [docs/CONFIGURATION.md](./docs/CONFIGURATION.md) | OneBot / Telegram / QQ 官方机器人、LLM、完整 `config.yaml` 字段、环境变量、进阶功能开关 |
| 配置 | [config.example.yaml](./config.example.yaml) | 带注释的配置模板（复制为 `config.yaml`） |
| 开发 | [ARCHITECTURE.md](./ARCHITECTURE.md) | 系统架构、进程模型、技术栈、核心模块 |
| 开发 | [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) | 生产部署：向量检索初始化、PostgreSQL 迁移、断连恢复 |
| 开发 | [docs/FRONTEND.md](./docs/FRONTEND.md) | 前端开发、Web UI 页面与目录结构 |
| 开发 | [docs/API.md](./docs/API.md) | HTTP API 与 WebSocket 端点清单 |
| 开发 | [docs/PACKAGING.md](./docs/PACKAGING.md) | PyInstaller / Electron 打包与自动发布 |
| 开发 | [PLUGIN_DEV.md](./PLUGIN_DEV.md) | 插件开发：Matcher / Rule / 生命周期 / DI / 工具 |
| 开发 | [docs/CODING_STANDARDS.md](./docs/CODING_STANDARDS.md) | 类型 / 命名 / 分层 / Git 约定与质量门禁 |
| 开发 | [docs/PROJECT_STRUCTURE.md](./docs/PROJECT_STRUCTURE.md) | 目录职责与产物归属 |
| 治理 | [CONTRIBUTING.md](./CONTRIBUTING.md) | 贡献流程、开发环境、本地构建与发版 |
| 治理 | [SECURITY.md](./SECURITY.md) | 安全策略、漏洞报告与最佳实践 |
| 治理 | [CHANGELOG.md](./CHANGELOG.md) | 版本变更记录 |
| 治理 | [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) | 社区行为准则 |
| 治理 | [LICENSE](./LICENSE) / [NOTICE](./NOTICE) | GPLv3 官方全文 / 附加条款与版权授权声明 |

## 合规声明

**本项目不包含任何针对 QQ、QQ 客户端或腾讯即时通信服务的逆向工程、协议破解、抓包分析、客户端注入、Hook 或模拟登录。**

Qingci-Bot CE 是一个通用机器人框架：它只通过**公开的、由第三方协议端实现的 OneBot 11 / OneBot 12 标准接口**（反向 WebSocket / HTTP API）、**QQ 开放平台面向开发者的官方机器人接口**（`q.qq.com`，AppID + AppSecret 鉴权）以及 Telegram Bot API 收发消息。QQ 侧的消息收发由使用者自行选择：或自行部署协议端（如 NapCat、LLBot / LLOneBot、Lagrange.OneBot、go-cqhttp 等），或在开放平台注册官方机器人后填入 AppID / AppSecret 走官方接口。本项目：

- 不内置、不捆绑、不分发任何协议端，也不参与其登录、鉴权或协议实现；
- 不获取、不存储、不破解任何 QQ 账号凭据或加密协议；
- 不修改、注入或附加到 QQ 客户端进程，也不与之直接通信；
- 不使用任何非公开接口或未文档化的私有协议。

**关联性与商标**：本项目与腾讯科技（深圳）有限公司及 QQ 产品无任何关联，未获其授权、认可或背书。「QQ」「腾讯」「OneBot」「Telegram」等名称仅用于说明协议兼容性，相关商标与权利归各自权利人所有。

**使用者责任**：使用者应自行确认其协议端的获取、部署与使用符合适用法律法规、平台服务条款及账号规则，并自行承担由此产生的全部责任；本项目作者不对使用者的具体使用方式负责。

> 「QQ 机器人官方接入」指接入 QQ 开放平台面向开发者的官方机器人接口（已实现，见 `platforms.qq`），与协议端方案并列，同样不涉及任何逆向或非公开接口。

## 近期开发计划

以下为方向性计划，用于说明近期工作重点，**不构成功能承诺或交付时间保证**；实际进度以 [CHANGELOG.md](./CHANGELOG.md) 与 GitHub Release 为准。

| 方向 | 内容 |
| --- | --- |
| 去冗余瘦身 | 精简运行时依赖与重复实现：收敛历史兼容代码、合并重复的消息段 / 协议转换路径、清理无调用方的旧分支，压缩 EXE / deb / rpm / Docker 镜像体积，并同步评估可移除的可选依赖 |

> 计划项会先在 CHANGELOG 的 `[Unreleased]` 段记录，发版时归入对应版本；涉及破坏性变更会提前在 Release Notes 说明。
>
> **去冗余瘦身 · 首轮进展**：已把 `installer.py` / `market.py` / `deps.py` 三处重复的子进程超时控制流收敛到 `bot/plugin/_proc.py` 的 `run_command()`，把 CQ 码序列化合并为 `bot/core/message.py` 的单一实现，并扩充 PyInstaller 排除项。依赖审计结论：现有 18 个运行时依赖均**不可安全移除** —— `aiosqlite` 是 SQLAlchemy 连接串里的动态驱动、`filelock` 是防版本降级的安全钉子、`websockets` 由 `uvicorn[standard]` 传递依赖且刚调整过上界，其余均有直接 import。
>
> **Chromium / Playwright 按需化 · 评估结论**：渲染相关资源约占服务端包未压缩体积的 39%（Chromium 285 MB + node driver 157 MB），构建侧已有 `--no-browser`（`SKIP_BROWSER=1`）与 Release 的 `bundle_browser` 输入可产出不含浏览器的轻量包；但打包产物（deb/rpm 安装后 `/opt` 只读、包内无 playwright CLI）目前**无法事后补装浏览器**，且渲染在全仓只有 1 个可降级的调用点（`/help` 图片化）。综合评估后**默认维持捆绑**（开箱可渲染），轻量包通道保留给确有需要的自构建用户；详见 [docs/PACKAGING.md](./docs/PACKAGING.md)。

## 许可证

[GNU General Public License v3.0 or later (GPL-3.0-or-later)](./LICENSE)；衍生作品须同样以 GPLv3 或更高版本开源。

此外，本项目依据 **GPLv3 第 7 条**添加了以下附加条款（完整文本见 [NOTICE](./NOTICE) 的 *ADDITIONAL TERMS UNDER GNU GPLv3 SECTION 7*）：

1. **保留声明**：分发时须保留源码中的版权、作者与项目来源声明，不得删除或篡改；
2. **修改版须改名**：修改后的作品必须以明显区别于「Qingci-Bot CE」的名称分发，并显著标注为修改版；
3. **不得暗示背书**：不得为宣传目的使用本项目或原作者名称，也不得明示或暗示获得原作者 / Qingci-Bot 组织的认可、赞助或关联。

> 这些条款对应 GPLv3 第 7 条允许的「保留合理法律声明」「标记修改版」「禁止误示来源 / 限制以许可人名义宣传」类别。包元数据中的 SPDX 表达式仍为 `GPL-3.0-or-later`，附加条款以 NOTICE 文件为准。
>
> 所有源码文件均带 `Copyright (C) 2026 Zhou Zhe (aka luoqingci)` 与 `SPDX-License-Identifier: GPL-3.0-or-later` 声明，由 `scripts/license_headers.py --check` 在 CI 中校验；新增源码文件请运行 `--fix` 补齐。
