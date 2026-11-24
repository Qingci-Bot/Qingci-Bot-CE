# HTTP API 参考

> 端点清单按路由模块分组，路径已包含 `api/server.py` 注册的 prefix；来源为
> `api/routes/`、`api/audit.py` 与 `api/server.py` 中的 `@app.websocket` 注册。
> 鉴权规则见 [SETUP.md](./SETUP.md#api-鉴权)，指标端点见 [SETUP.md](./SETUP.md#监控指标prometheus)。


所有接口前缀 `/api`。启用鉴权（配置 `api_key`）时，除 `/api/bot/status`、`/api/bot/health`、`/api/auth/*`、`/api/config/wizard*`（首次启动向导）外均需携带 `X-API-Key` 请求头；`api_key` 为空时全部免鉴权。

**错误响应与超时说明：**

- `/api/bot/start`、`/stop`、`/restart` 有超时保护（启动 30s / 停止 15s），超时返回 `504` 并自动尝试清理残留资源，可通过 `/api/bot/status` 确认实际状态

- `/api/plugin/load` 请求体必须为 JSON 且包含字符串字段 `module_path`，类型非法时返回 `422`

- 所有 `5xx` 错误的 `detail` 为通用文案（不暴露内部异常细节），详细原因见服务端日志

## Bot 控制 `/api/bot`

| 方法   | 路径         | 鉴权 | 说明          |
| ---- | ---------- | -- | ----------- |
| GET  | `/status`  | 否  | 获取 Bot 运行状态 |
| GET  | `/health`  | 否  | 健康检查        |
| POST | `/start`   | 是  | 启动 Bot      |
| POST | `/stop`    | 是  | 停止 Bot      |
| POST | `/restart` | 是  | 重启 Bot      |

### `GET /api/bot/status` 响应

```jsonc
{
  "running": true,
  "connected": true,            // 主平台（onebot）是否在线
  "version": "1.22.3-rc2",
  "last_heartbeat": 1763... ,   // epoch 秒；0 表示无心跳机制
  "render": {                   // HTML 渲染能力探测
    "enabled": true,            //   配置开关 render.enabled
    "supported": true,          //   运行环境是否可能支持（playwright 可导入）
    "available": true,          //   探测后真实可用（浏览器可启动）
    "reason": "",               //   不可用原因（未安装 / 浏览器缺失等）
    "checked": true             //   是否已完成后台探测
  },
  "platforms": [
    {
      "name": "qq",                  // 适配器标识，与事件的 platform 字段一致
      "display_name": "QQ 官方机器人",
      "connected": true,
      "last_heartbeat": 1763...,
      "self_id": 9000001,            // 平台侧机器人自身 ID
      "reason": "",                  // 未启用/启动失败原因；未配置凭据时为可操作的指引文案
      "connection_state": "connected",   // connected / degraded / connecting / stopped
      "consecutive_errors": 0,
      "error_count": 0,
      "last_error_time": 0,
      "last_disconnect_time": 0,
      "backoff": 2.0,
      // ↑ 以上为各适配器通用健康字段；以下为平台特有字段（示例为 QQ）
      "sandbox": false,
      "app_id": "1000001",
      "username": "测试机器人",
      "session_resumable": true,     // 网关会话可 Resume（断连补发）
      "token_expire_at": 1763...,    // access_token 到期时间
      "known_sessions": 12           // 已登记的 openid ↔ 内部 ID 映射数量
    }
  ],
  "plugins": [ { "name": "...", "status": "loaded", "enabled": true, "pages": [] } ]
}
```

要点：

- `platforms` 只包含**已启用**的适配器（主平台停用时不出现在列表里）；
- 通用健康字段由 `PlatformAdapter.status_info()` 附加，各平台可有不同扩展字段：Telegram 有 `identity_dirty`，QQ 有 `sandbox` / `session_resumable` / `token_expire_at` / `known_sessions`；
- `reason` 非空表示该适配器未在线的原因：以「未配置」开头的是正常状态（用户还没填凭据，Web UI 显示「未配置」而非「离线」），其余为真实故障信息；
- `connected` 顶层字段只反映主平台（`onebot`），判断某个适配器是否在线请看 `platforms[].connected`。

### `GET /api/bot/health`

`{"status": "ok", "running": <bool>}`——仅表示 API 进程存活，不代表各平台在线。

## 配置管理 `/api/config`

| 方法   | 路径               | 鉴权 | 说明                                                  |
| ---- | ---------------- | -- | --------------------------------------------------- |
| GET  | `/`              | 是  | 获取完整配置（敏感字段脱敏为 `***`）                               |
| PUT  | `/`              | 是  | 更新配置（深度合并，`***` 占位符自动过滤）                            |
| GET  | `/bot`           | 是  | 获取 Bot 配置                                           |
| PUT  | `/bot`           | 是  | 更新 Bot 配置                                           |
| GET  | `/llm`           | 是  | 获取 LLM 配置                                           |
| PUT  | `/llm`           | 是  | 更新 LLM 配置（provider=custom 时强制校验 api\_url）           |
| GET  | `/llm/presets`   | 是  | 获取 LLM 提供商预设（api\_url + 推荐 model，切换 provider 自动联动）  |
| POST | `/llm/models`    | 是  | 查询提供商可用模型列表（按 provider 调用对应 API，10s 超时，失败 400 透传原因） |
| GET  | `/onebot`        | 是  | 获取 OneBot 配置                                        |
| POST | `/llm/test`      | 是  | 测试 LLM 连接（返回 `{available, message}`）                |
| GET  | `/wizard/status` | 否  | 首次启动向导状态（`needs_setup`）                             |
| POST | `/wizard`        | 否  | 提交首次启动配置（仅未完成向导时可用）                                 |
| POST | `/wizard/skip`   | 否  | 跳过首次启动向导                                            |

## 插件管理 `/api/plugin`

| 方法     | 路径                   | 鉴权 | 说明                                                                                                         |
| ------ | -------------------- | -- | ---------------------------------------------------------------------------------------------------------- |
| GET    | `/`                  | 是  | 获取插件列表（含状态、分类、Web 管理页面入口）                                                                                  |
| GET    | `/{name}`            | 是  | 获取插件详情                                                                                                     |
| POST   | `/{name}/reload`     | 是  | 重载插件                                                                                                       |
| POST   | `/load`              | 是  | 加载外部插件（仅允许 `plugins.*` / `bot.plugin.builtin.*` 白名单前缀）                                                     |
| DELETE | `/{name}`            | 是  | 卸载插件（默认删代码目录、保留数据与依赖；`?purge=true` 彻底删除——一并删除数据目录与第三方依赖，不可恢复；内置插件 chat/admin/help/imagegen/knowledge 不可卸载） |
| POST   | `/{name}/disable`    | 是  | 禁用插件（保留实例，跳过事件分发）                                                                                          |
| POST   | `/{name}/enable`     | 是  | 启用插件（恢复事件分发）                                                                                               |
| GET    | `/{name}/metrics`    | 是  | 获取插件执行指标（调用次数、平均耗时、错误率）                                                                                    |
| GET    | `/{name}/config`     | 是  | 获取插件配置 JSON Schema 与当前值（用于自动渲染配置表单）                                                                        |
| PUT    | `/{name}/config`     | 是  | 更新插件配置（写入 config.yaml 并应用到插件实例）                                                                            |
| GET    | `/discover/metadata` | 是  | 无导入发现：扫描插件目录根层 `.py` 文件旁的同名 `plugin.json`（文件型插件）                                                           |

## 插件市场 `/api/plugins/market`

| 方法   | 路径         | 鉴权 | 说明                         |
| ---- | ---------- | -- | -------------------------- |
| GET  | `/`        | 是  | 拉取市场索引（含缓存 TTL）            |
| GET  | `/info`    | 是  | 市场源信息（url / mirror / 更新时间） |
| POST | `/install` | 是  | 安装市场插件（按 `name`）           |
| POST | `/update`  | 是  | 更新已安装插件                    |
| POST | `/refresh` | 是  | 强制刷新索引缓存                   |
| GET  | `/source`  | 是  | 获取当前市场源                    |
| PUT  | `/source`  | 是  | 修改市场源（url / mirror\_url）   |

## 命令管理 `/api/command`

| 方法  | 路径                   | 鉴权 | 说明                                                    |
| --- | -------------------- | -- | ----------------------------------------------------- |
| GET | `/conflicts`         | 是  | 列出所有已注册命令，自动标记同名冲突                                    |
| PUT | `/{owner}/{command}` | 是  | 更新命令：`disabled`（bool）禁用/启用，`priority`（int 0–100）调整优先级 |

## 消息日志与用量 `/api/log`

| 方法     | 路径                   | 鉴权 | 说明                                                                         |
| ------ | -------------------- | -- | -------------------------------------------------------------------------- |
| GET    | `/messages`          | 是  | 搜索消息记录                                                                     |
| GET    | `/messages/count`    | 是  | 获取消息总数                                                                     |
| GET    | `/messages/export`   | 是  | 导出消息记录（CSV 流式，utf-8-sig，Excel 直接打开不乱码）                                     |
| GET    | `/usage`             | 是  | LLM 用量统计（依赖 `log.usage_tracking`）                                          |
| DELETE | `/messages`          | 是  | 删除消息记录；支持 `user_id` / `group_id` / `before_days` 过滤，全部删除需显式 `confirm=true` |
| DELETE | `/sessions`          | 是  | 清除所有会话（需 `confirm=true`）                                                   |
| GET    | `/sessions`          | 是  | 会话列表（按最后活跃排序，含条数与归属 QQ）                                                    |
| GET    | `/sessions/messages` | 是  | 查看指定会话历史（`?key=private:10001` 或 `group:10001:20002`）                       |
| DELETE | `/sessions/one`      | 是  | 删除指定会话（`?key=会话key`，带审计）                                                   |

## 群配置 `/api/group`

| 方法     | 路径            | 鉴权 | 说明             |
| ------ | ------------- | -- | -------------- |
| GET    | `/list`       | 是  | 群配置列表          |
| GET    | `/{group_id}` | 是  | 获取单群配置         |
| PUT    | `/{group_id}` | 是  | 更新群配置（Bot 开关等） |
| DELETE | `/{group_id}` | 是  | 删除群配置（带审计埋点）   |

## 登录与鉴权 `/api/auth`

| 方法   | 路径        | 鉴权 | 说明                                     |
| ---- | --------- | -- | -------------------------------------- |
| GET  | `/status` | 否  | 是否需要登录（配置 api\_key 是否非空）               |
| POST | `/login`  | 否  | 登录；按来源 IP 防暴力限流，连续失败 5 次后冷却 60 秒返回 429 |

## 数据库备份 `/api/backup`

| 方法   | 路径            | 鉴权 | 说明                                                          |
| ---- | ------------- | -- | ----------------------------------------------------------- |
| POST | `/db`         | 是  | 在线备份到 `data/backups/`（sqlite backup API，文件名带随机后缀，保留最近 10 份） |
| GET  | `/db/download` | 是  | 下载备份（文件名白名单防路径穿越，文件名须以 `qingci-bot_` 开头、`.db` 结尾） |
| POST | `/restore`    | 是  | multipart 上传恢复：完整性校验 → 自动备份当前库 → 原子替换，现有会话自动失效 |

## 审计日志 `/api/audit`

| 方法  | 路径      | 鉴权 | 说明                             |
| --- | ------- | -- | ------------------------------ |
| GET | `/logs` | 是  | 审计日志倒序查询（配置变更 / 启停 / 登录 / 备份等） |

## 实例管理 `/api/instances`

| 方法     | 路径              | 鉴权 | 说明                            |
| ------ | --------------- | -- | ----------------------------- |
| GET    | `/`             | 是  | 实例列表（含端口、主平台、数据占用）            |
| GET    | `/root`         | 是  | 实例根目录绝对路径（`{path, exists, writable}`）；Web UI「实例管理」页顶部据此展示并支持复制 |
| POST   | `/`             | 是  | 创建实例（指定名称与主平台）                |
| DELETE | `/{name}`       | 是  | 删除实例                          |
| PUT    | `/{name}`       | 是  | 重命名实例（同步重命名目录与 instance.json） |
| POST   | `/{name}/start` | 是  | 切换/启动到指定实例（跨进程重启）             |

## WebSocket

| 路径               | 鉴权                                                        | 说明                                                                                                                                        |
| ---------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/ws/log`    | `token` 查询参数或 `sec-websocket-protocol: api-key.<key>` 子协议 | 实时推送消息记录，连接后自动接收新消息；60s 心跳保活（90s 无消息断开），连接数上限 32                                                                                          |
| `/api/ws/chat`   | 同上                                                        | 对话调试台：客户端发送 `{"message": "...", "user_id": 900000001}`，服务端逐块返回 `{"type":"delta","text":...}`，结束返回 `{"type":"done"}`；流式调用 LLM，独立连接池（上限 32） |
| `/api/ws/runlog` | 同上                                                        | 运行日志：连接即回发环形缓冲快照（`{"type":"snapshot"}`），随后实时推送 `{"type":"log"}` 条目；30s 心跳保活                                                               |
