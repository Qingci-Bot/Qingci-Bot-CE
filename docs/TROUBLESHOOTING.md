# 故障排查

> 按现象定位：先确认进程与接口，再看状态与日志。启动方式见 [SETUP.md](./SETUP.md)，
> 配置字段见 [CONFIGURATION.md](./CONFIGURATION.md)，端点见 [API.md](./API.md)。

## 先看这三处

1. `GET /api/bot/health`（免鉴权）返回 200 → 后端进程与 API 服务正常；
2. `GET /api/bot/status`（免鉴权）→ OneBot 连接状态、LLM 可用性、消息记录数与渲染能力；
3. 运行日志：Web UI「运行日志」页（受 `log.run_log_enabled` 控制，关闭后页面提示无数据）；
   文件日志由 `log.log_file_enabled` 开启，落在 `log_dir/qingci-bot.log`，按 `log_file_max_bytes` 轮转。

## 协议端连不上

- 本项目是**反向 WebSocket 服务端**：连接由协议端（NapCat / LLBot / Lagrange 等）发起，
  断连后的重连也由协议端驱动，本项目只监测状态与恢复收发。先查协议端日志，不要在本项目里找「主动重连」开关。
- `onebot.host` 默认 `127.0.0.1`。Docker 容器或外部机器上的协议端要连入，需改成 `0.0.0.0`，
  并确认 `onebot.port`（默认 3001）与 `access_token` 两端一致。
- OneBot 12 原生适配器是**独立端口**（`platforms.onebot12.port`，默认 3002），
  且要 `platforms.onebot12.enabled: true`，不要与 OneBot 11 的 3001 混用。
- Telegram 是长轮询客户端，由本项目按指数退避重连；Token 无效时看 API 错误分类（401/403）。
- 断连期间 Web UI 与 API 仍可用，已加载插件与配置不变；恢复细节见 [DEPLOYMENT.md](./DEPLOYMENT.md#协议端断连恢复)。

## Web UI 与鉴权

- 页面打不开：确认 `--host` / `--port`（默认 `127.0.0.1:8080`）、`web/dist` 存在、进程未以 `--no-bot` 之外的异常状态退出。
- 配置 `api_key` 后**所有接口（含 GET）**都要带 `X-API-Key`。免鉴权的只有：
  `GET /api/bot/status`、`GET /api/bot/health`、`GET /api/auth/status`、`POST /api/auth/login`、
  `GET /api/config/wizard/status`、`POST /api/config/wizard`、`POST /api/config/wizard/skip`。
  Web UI「系统设置」可同时配置服务端 Key 与浏览器端 Key；WebSocket 用 `token` 查询参数或
  `sec-websocket-protocol: api-key.<key>` 子协议。
- 首次启动被跳到 `/setup`：`/api/config/wizard/status` 的 `needs_setup` 为真，完成或跳过向导即可。

## 配置被「重置」

`ConfigManager.load()` 在 YAML 语法错误或字段校验失败时，会**先备份损坏文件**，再回退到默认配置并写回。
如果修改 `config.yaml` 后发现配置回到默认值，去看日志里的「配置文件 YAML 格式错误」或「配置字段验证失败」，
并从备份文件恢复。另外：

- 环境变量只在**进程启动时**读取，修改后需重启；
- 数据目录：安装版由向导选定，AppImage / 容器用 `QINGCI_DATA_HOME` 指向可写位置；
- 一键部署、CLI 参数与实例目录布局见 [SETUP.md](./SETUP.md)。

## 能力自动降级

| 现象 | 原因与处理 |
| --- | --- |
| 签到卡等 HTML 渲染不生效 | `playwright` 或 Chromium 缺失时渲染能力自动降级、不影响启动。源码运行需装 `[render]` 分组并下载 Chromium（国内可设 `PLAYWRIGHT_DOWNLOAD_HOST` 走镜像）；`/api/bot/status` 的 `render` 字段显示能力状态 |
| RAG 语义检索没效果 | `mode: vector` 需要 `lancedb`；未安装时自动回退 `keyword` 并告警。embedding 模型需与建索引时一致，且当前为全量重建，详见 [DEPLOYMENT.md](./DEPLOYMENT.md#向量检索rag初始化) |
| 聊天回复「AI 服务暂时不可用」 | LLM 调用返回空/失败。用 `POST /api/config/llm/test` 测连接，核对 `llm.api_key` / `api_url` / `provider` 联动与 `llm.timeout`、`num_retries`；`ollama` 可免 key |
| `/image`、`/kb` 等命令无反应 | 对应功能开关默认关闭（`image.enabled` / `rag.enabled`），且命令需相应权限；见 [CONFIGURATION.md](./CONFIGURATION.md#进阶功能说明功能开关均默认关闭) |

## 插件

- **依赖安装失败**：`bot.auto_install_plugin_deps` 关闭可降低供给链风险，但缺依赖时插件会加载失败；
  依赖装在实例隔离的 `deps/` 目录，卸载插件不自动清理。
- **来源被拒**：远程来源仅允许 http(s) / git 且经 SSRF 校验（拦截私网/环回地址）；
  本地目录/归档需显式 `allow_local=True`，默认拒绝以防远端索引触达本地路径。
- **命令不生效**：依次检查 `bot.trigger_mode`（at / keyword / always）、群/用户黑名单、
  插件是否被禁用（禁用保留实例但跳过分发）、命令冲突与优先级（`/api/command/conflicts`）。
- 热重载只在 `hot_reload.enabled` 开启时生效，生产环境建议关闭。

## 性能与数据

- **事件积压 / 丢弃**：`bot.event_concurrency` 控制并发、`bot.max_pending_events` 控制排队上限；
  Prometheus 的 `qingci_bot_events_dropped_total` 持续增长说明上限偏小，指标清单见
  [SETUP.md](./SETUP.md#监控指标prometheus)。
- **慢 handler**：`log.slow_handler_threshold_ms`（0 或负数关闭）超阈值记 WARN 并计入插件 `slow_count`。
- **启动时的迁移漂移告警**：表示模型定义与迁移脚本不一致，应补齐迁移；
  `QINGCI_SKIP_MIGRATION_CHECK=1` 只是跳过检查（每次启动约省 1 秒），**不修复**不一致。
- **SQLite 写锁瓶颈**：高频群聊场景可迁移到 PostgreSQL，见 [DEPLOYMENT.md](./DEPLOYMENT.md#数据库迁移至-postgresql)。
- **备份/恢复**：`POST /api/backup/db` 与 `POST /api/backup/restore`；数据保留期由 `log.retention_days` 控制（0 = 不清理）。

## Docker

- 容器以 uid 1000 的 `appuser` 运行，宿主机挂载到 `instances/` 的目录需授权给该 uid，否则配置/数据库不可写。
- 容器内 `onebot.host` 默认 `127.0.0.1`；外部协议端连入要改 `0.0.0.0` 并映射 3001（OneBot 12 另需 3002）。
- 用 `QINGCI_DATA_HOME` 把数据放到可写卷；健康检查走 `/api/bot/health`。

## 仍定位不到

请带上以下信息到 [GitHub Issues](https://github.com/Qingci-Bot/Qingci-Bot-CE/issues) 反馈：
版本、`python --version`、启动方式（源码 / Docker / EXE）、`GET /api/bot/status` 输出、
相关日志片段（**请先脱敏** `api_key` / `access_token` / Token 等密钥）。
安全漏洞不要公开提交，按 [SECURITY.md](../SECURITY.md) 的私密渠道报告。
