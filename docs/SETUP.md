# 安装、运行与运维

> 面向部署与运维：环境要求、安装、本地 / Docker / Linux 三种启动方式、命令行参数、
> 管理命令、API 鉴权与 Prometheus 监控。配置项（协议端、LLM、config.yaml 全字段、
> 环境变量、进阶开关）见 [CONFIGURATION.md](./CONFIGURATION.md)；开发自检命令见
> [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 环境要求

- Python 3.11+（推荐 3.12）

- 任意 OneBot 11 协议端（如 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot) / NapCat / go-cqhttp）

- Node.js 22+（仅构建 Web UI 时需要；vite 8 / ESLint 10 / jsdom 31 要求 Node ^20.19 或 ≥22.12，CI 与构建脚本统一用 22；`web/dist` 已存在可跳过）

- 桌面 GUI 由 Electron 提供：构建桌面壳需 Node.js 22+（`desktop/electron`，Electron 45.2.x，与 CI 的 Node 22 一致）；最终用户运行 `build-electron.bat` 产出的安装版 / 绿色解压版 EXE 无需额外运行时

## 安装

> **推荐**：`uv sync --locked --extra dev` —— 一条命令按 `uv.lock` 精确安装全部开发依赖，与 CI 完全一致。
> `uv pip install` 会重新解析版本，可能与锁文件不一致，仅在只想装部分可选依赖时使用。

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
> | 分组         | 安装命令                            | 内容                                                                             |
> | ---------- | ------------------------------- | ------------------------------------------------------------------------------ |
> | 核心         | `uv pip install -e .`           | 运行时依赖（FastAPI、litellm、OneBot、qingci-plugin-sdk 等）                              |
> | `[vector]` | `uv pip install -e ".[vector]"` | 向量知识库（lancedb，可选；缺失时 RAG 自动回退关键词检索）                                            |
> | `[render]` | `uv pip install -e ".[render]"` | HTML → 图片渲染（playwright，可选；安装与 Chromium 下载见下方「启用 HTML 渲染」；缺失时渲染能力自动降级不可用，调用方回退） |
> | `[test]`   | `uv pip install -e ".[test]"`   | pytest / pytest-asyncio / pytest-cov / httpx                                   |
> | `[build]`  | `uv pip install -e ".[build]"`  | pyinstaller + playwright（`.\build.bat` 依赖；playwright 用于打包时内置无头浏览器）             |
> | `[dev]`    | `uv pip install -e ".[dev]"`    | 以上全部 + ruff / mypy（代码质量工具）                                                     |
>
> **启用 HTML 渲染**：源码运行时安装 `[render]` 分组后还需下载 Chromium（国内网络建议走 npm 镜像，否则容易卡住/超时）：
>
> ```bat
> set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
> uv run playwright install chromium
> ```
>
> **打包版（EXE）已全内置**：`build.bat` 构建时自动下载无头 Chromium 到产物目录 `ms-playwright\`，运行时经 `PLAYWRIGHT_BROWSERS_PATH` 定位，EXE 开箱即可渲染签到卡，无需最终用户再执行安装。构建期同样可用上述 `PLAYWRIGHT_DOWNLOAD_HOST` 走国内镜像。
>
> **桌面壳打包另见**：`build.bat` 产出的 onedir（`dist\qingci-bot-ce\`）与 `build-electron.bat`（electron-builder，产出 NSIS 安装版 Setup.exe + 绿色解压 zip）。**实例数据放在安装时选定的数据目录**（安装向导有一步「选择数据目录」，默认 `%LOCALAPPDATA%\Qingci-Bot CE\data`，选择记在注册表 `HKCU\Software\Qingci-Bot CE` 与 `%APPDATA%\Qingci-Bot CE\datadir.txt`），该目录**独立于安装目录**——升级与卸载只清理 `$INSTDIR`，不会碰到数据；旧版本把数据放在 EXE 旁的 `instances/`，首次启动会自动迁移过去（同卷 rename，跨卷复制并保留原目录）。桌面壳需 Node.js 22+（Electron 45.2.x，与 CI 的 Node 22 一致），用户运行安装版/绿色解压版无需额外运行时。后端本体以**单个归档**随包分发，安装阶段只写 1 个大文件（避免数千个小文件逐个落盘导致的安装缓慢），首次启动时解压到**数据目录**下的 `backend/<版本>/`（一次性，视磁盘约十几秒，期间显示启动页）；升级到新版本会重新解压并清理旧目录。
>
> **数据都放在哪**：数据目录（安装时选定）下有 `instances/<实例>/`（配置、数据库、日志）与 `backend/`（后端解压产物）；`%APPDATA%\Qingci-Bot CE\` 只留 Electron 自身的浏览器缓存，`%APPDATA%\Qingci-Bot CE\datadir.txt` 记录你选的数据目录。
>
> **Linux 桌面（AppImage）**：与 Windows 同源，Linux 后端由 `build-linux.sh`（`build.bat` 的对应脚）产出 onedir，再经 `electron-builder --linux` 打成 AppImage。AppImage 无安装向导，数据目录回退为 EXE 旁的 `instances/`（可用环境变量 `QINGCI_DATA_HOME` 覆盖）。
>
> **发布流水线（GitHub Actions）**：打 `vX.Y.Z` tag 即自动构建并上传分发包到同名 GitHub Release，无需手动上传——`build.bat`/`build-linux.sh` 分别出后端、`build-electron.bat`/`electron-builder --linux` 出 EXE / AppImage，同时构建并推送 Docker 镜像到 `ghcr.io/<repo>`（tag 含 `vX.Y.Z` / `X.Y` / `X` / `latest`）。见 [.github/workflows/release.yml](../.github/workflows/release.yml)。
>
> 浏览器缺失或下载失败时渲染能力自动降级不可用（`/api/bot/status` 的 `render` 字段可查状态），不影响框架启动。
> 插件协议层 SDK（`qingci-plugin-sdk`）自 1.15.2 起由官方 PyPI 分发，在 `pyproject.toml` 里钉成精确版本、随核心依赖一起安装；无需令牌或额外索引配置，版本由依赖声明与 `uv.lock` 共同锁定。本地开发时若需对 SDK 改代码，可 `uv pip install -e ..\Plugins-SDK` 覆盖。
>
> 若跳过 `pyproject.toml`，可手动安装核心依赖（SDK 用 `pip install qingci-plugin-sdk==1.15.2` 安装）：
>
> ```bash
> uv pip install fastapi "uvicorn[standard]" websockets aiocqhttp aiohttp aiosqlite \
>   sqlmodel alembic "sqlalchemy[asyncio]" litellm pydantic pyyaml httpx \
>   "qingci-plugin-sdk==1.15.2" "apscheduler>=3.10,<4" "mcp>=1.6,<2" \
>   --python .venv\Scripts\python.exe
> ```

## 启动

```bash
# 仅 API + Web UI（不启动 Bot）
.venv\Scripts\python main.py --no-bot

# 完整启动 Bot + API
.venv\Scripts\python main.py

# 自定义端口
.venv\Scripts\python main.py --port 9000

# 以指定实例启动（instances/<name>/ 自包含目录：config.yaml + data/ + plugins/）
.venv\Scripts\python main.py --instance <name>
```

> 桌面 GUI 通过 **Electron 壳**（`desktop/electron`，打包见 `build-electron.bat`）运行：壳 spawn Python 后端（`main.py --backend`）并加载其 Web UI，系统托盘/单实例/启动画面均由 Electron 承担，不再使用 `--desktop`/pywebview。

启动必须绑定一个实例（无全局模式）：未指定 `--instance` 时自动选择默认实例（`default` 优先，其次名称排序第一个）；若实例数为 0 则自动创建 `default` 实例。每个实例是 `instances/<name>/` 下的自包含目录，独立「实例管理」页（`/instances`）支持新建/删除/切换/重命名实例（含端口、启用的适配器、数据占用等信息）；切换会以目标实例重启进程。**创建实例时可绑定主平台**（OneBot / OneBot 12 / Telegram）：创建后系统设置即针对该平台语义落位——OneBot 主平台实例启动反向 WS 服务端（`onebot.enabled`），Telegram 主平台实例自动关闭反向 WS 并启用 Telegram 适配器，`super_admin` / 管理员 / 黑白名单均以平台无关字符串 ID 配置。

启动后访问 `http://127.0.0.1:8080/ui` 进入管理界面。

> **Web UI 未构建时**：若 `web/dist` 缺失或不完整，访问 `/` 会返回构建提示页（引导在 `web/` 目录执行 `npm install` 与 `npm run build`），API 服务本身仍正常可用。克隆仓库后首次启动前请先构建前端。

### Docker 容器部署（推荐，Linux 最省心）

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

> 构建依赖 `qingci-plugin-sdk`（`pyproject.toml` 里的 PyPI 固定版本依赖）需构建期能访问 pypi.org；离线环境可先把该 wheel 下载到本地并临时改为本地路径。

### Linux 源码部署（一键脚本 install.sh）

核心运行（Bot + WebUI/API）为纯 Python，无需任何 GUI 系统依赖，Linux/macOS 均可直接跑：

```bash
chmod +x install.sh
./install.sh                        # 核心依赖（自动检测 Python>=3.11，优先 uv，否则 pip）
./install.sh --vector               # 追加向量知识库（lancedb）
./install.sh --with-gui             # 历史参数（桌面 GUI 已由 Electron 壳承担，保留仅为兼容，实际不再安装）
./install.sh --dev                  # 追加测试/构建/质量工具
```

脚本会：自动安装系统依赖（git / 编译工具；需 root，可用 `SKIP_SYS_DEPS=1` 跳过）→ 创建 `.venv` → 安装核心依赖。装完启动：

```bash
.venv/bin/python main.py --instance default
```

- WebUI：`http://127.0.0.1:8080/ui`

- 编辑实例配置：`instances/default/config.yaml`

- 外部 OneBot 前端连入前，把该实例 `onebot.host` 改为 `0.0.0.0`

- **桌面 GUI**：通过 Electron 壳（`desktop/electron`）运行；Linux 下 Electron 需要系统动态库（Debian 系含 `libgtk-3`/`libnss3`/`libasound2` 等，可用 `npx electron-builder` 文档对照）。**建议 Linux 优先使用 Docker 或 Headless + WebUI 模式**

- 单实例保护：Electron 侧按 data-dir 退化的控制端口实现——同一实例（同 data-dir）重复启动聚焦已有窗口，不同实例（不同 data-dir）可并行多开；CLI/Headless 模式基于命名互斥量，Linux 下自动降级（允许多开，不阻塞启动）

## 命令行参数

| 参数                   | 说明                                                 | 默认值                     |
| -------------------- | -------------------------------------------------- | ----------------------- |
| `--no-bot`           | 仅启动 API 服务                                         | -                       |
| `--backend`          | 由 Electron 壳拉起的后端进程（仅桌面壳用；就绪时打印机器可读端口）             | -                       |
| `--resolve-instance` | 仅解析实例元数据并打印 JSON（供 Electron 壳预探测），不启动服务            | -                       |
| `--port`             | API 端口                                             | 实例元数据端口（首个实例 8080，后续递增） |
| `--host`             | API 监听地址                                           | 127.0.0.1               |
| `--config`           | 配置文件路径                                             | 实例内 `config.yaml`       |
| `--instance`         | 启动到指定实例（`instances/<name>/` 自包含目录）                 | 默认实例                    |
| `--data-dir`         | 指定可写数据根目录（DB/日志/插件数据等），用于多实例隔离；未指定时默认落在实例内 `data/` | `instances/<name>/data` |

## 管理命令

管理员分为两级：**超级管理员**（唯一，`bot.super_admin`）与**普通管理员**（多个，`bot.admin_users`，超级管理员自动继承普通管理员权限）。

**普通管理员命令**（`bot.admin_users` 中的平台无关用户 ID，如 QQ 号 / Telegram 用户 ID）：

| 命令        | 说明                                       |
| --------- | ---------------------------------------- |
| `/status` | 查看 Bot 运行状态（OneBot 连接 / LLM 可用性 / 消息记录数） |
| `/clear`  | 清除当前会话历史                                 |

**超级管理员命令**（仅 `bot.super_admin` 对应的平台无关用户 ID）：

| 命令                                      | 说明                                                    |
| --------------------------------------- | ----------------------------------------------------- |
| `/blacklist add <用户ID>`                 | 添加用户到黑名单（平台无关用户 ID）                                   |
| `/blacklist remove <用户ID>`              | 从黑名单移除用户                                              |
| `/filter on\|off\|reload`               | 敏感词过滤开关 / 重载词库（词库为空时会提示编辑 `data/sensitive_words.txt`） |
| `/group on\|off`                        | 当前群 Bot 开关                                            |
| `/kb add\|list\|search\|remove\|reload` | 知识库管理（需开启 `rag.enabled`）                              |

**所有用户可用命令**（无需管理员权限）：

| 命令                      | 说明                                                                    |
| ----------------------- | --------------------------------------------------------------------- |
| `/help`（或 `/帮助`）        | 按当前用户权限列出可用命令；渲染为图片发送（渲染不可用时回退纯文本，命令过多自动折叠）；`/help <插件名\|分类\|命令>` 可筛选 |
| `/image <提示词>`（或 `/画图`） | AI 绘图（需开启 `image.enabled`，成功后以图片消息回复）                                 |
| `/persona`              | 查看当前会话人格                                                              |
| `/persona 列表`           | 列出全部可用人格                                                              |
| `/persona <名称>`         | 切换当前会话人格（配置了 `llm.personas` 时可用）                                      |
| `/persona 重置`           | 恢复默认人格或 `system_prompt`                                               |

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

## 监控指标（Prometheus）

`GET /api/metrics` 以 Prometheus 文本格式（`text/plain; version=0.0.4`）导出进程内指标，**不查数据库**，可安全按 15s 间隔抓取。

指标为运营数据（含调用量、错误量、插件列表），与其他管理接口一样受 `api_key` 保护。抓取端需携带请求头：

```yaml
# prometheus.yml
scrape_configs:
  - job_name: qingci-bot
    metrics_path: /api/metrics
    authorization:            # 或直接用 http_headers 传 X-API-Key
      credentials: your-secret-key
    static_configs:
      - targets: ["127.0.0.1:8080"]
```

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `qingci_bot_up` / `_info` | gauge | 进程存活 / 版本标签 |
| `qingci_bot_running` / `_connected` | gauge | Bot 运行状态 / 协议端连接状态 |
| `qingci_bot_uptime_seconds` | gauge | 运行时长 |
| `qingci_bot_events_pending` | gauge | 排队 + 执行中的事件数 |
| `qingci_bot_events_dropped_total` | counter | 因积压超限丢弃的事件（持续增长说明 `bot.max_pending_events` 偏小） |
| `qingci_bot_message_buffer_pending` / `_dropped_total` | gauge / counter | 消息写入缓冲积压与丢弃 |
| `qingci_bot_plugins_loaded` | gauge | 已加载插件数 |
| `qingci_bot_matcher_calls_total` / `_errors_total` / `_slow_total` / `_avg_duration_ms` | counter / gauge | 按 `plugin`、`matcher` 标签细分 |
