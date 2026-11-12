# 前端开发

> Web UI 的开发命令、页面路由与目录结构。插件开发见 [PLUGIN_DEV.md](../PLUGIN_DEV.md)，前端编码约定见 [CODING_STANDARDS.md](./CODING_STANDARDS.md)。

```bash
cd web
npm install
npm run dev      # 开发模式（热更新，http://localhost:5173）
npm run build    # 构建生产版本到 web/dist/
```

开发模式下，前端请求会自动代理到 `http://127.0.0.1:8080`（在 `vite.config.js` 中配置）。

## Web UI 页面

| 页面     | 路由           | 说明                                                                         |
| ------ | ------------ | -------------------------------------------------------------------------- |
| 仪表盘    | `/`          | 运行状态卡片 + LLM 用量统计图表（依赖 `log.usage_tracking`）                               |
| LLM 配置 | `/config`    | 提供商切换（联动 api\_url/model）、模型列表拉取、人格管理、MCP 服务器配置、连接测试                        |
| 对话调试台  | `/lab`       | 无需进入 QQ 即可流式测试 LLM 回复；独立会话 key，不污染真实对话（走 `/api/ws/chat`）                   |
| 群配置    | `/groups`    | 各群 Bot 开关与触发模式                                                             |
| 插件管理   | `/plugins`   | 插件列表 / 详情 / 重载 / 加载外部插件 / 卸载 / 禁用 / 启用；内含「命令管理」Tab 与「插件市场」                 |
| 消息日志   | `/logs`      | 实时消息流 + 会话记录可视化（按会话分组查看/删除，支持清理与 CSV 导出）                                   |
| 运行日志   | `/runlog`    | 实时运行日志流 + 级别过滤（受 `log.run_log_enabled` 开关控制；关闭后页面提示无数据，走 `/api/ws/runlog`） |
| 系统设置   | `/settings`  | Bot/OneBot/平台适配器配置、服务端/浏览器 API Key、运行日志采集开关、数据库备份、登录审计                     |
| 实例管理   | `/instances` | 新建/删除/切换/重命名实例（含端口、启用的适配器、数据占用）                                            |
| 关于     | `/about`     | 版本信息                                                                       |
| 登录     | `/login`     | API Key 登录（服务端已配置 `api_key` 时显示）                                           |
| 首次启动向导 | `/setup`     | 首次启动引导（配置未完成时自动跳转）                                                         |

## 目录结构与脚本

| 路径 | 职责 |
| --- | --- |
| `web/src/api/request.js` | 统一请求层（API Key、错误与 `cause`） |
| `web/src/router/index.js` | vue-router 页面路由 |
| `web/src/views/` | 页面组件（Dashboard / Settings / PluginManager 等） |
| `web/src/components/` | 复用组件：`Drawer.vue`；`plugin/`（PluginManager 视图的职责分组：CommandTab / MarketTab / PluginConfigDrawer）；`settings/`（Settings 视图的卡片分组：DataManagementCard / AuditLogCard） |
| `web/src/stores/app.js` | Pinia 全局状态 |
| `web/src/composables/` | useWebSocket / useToast |
| `web/src/styles/main.css` | 全局样式 |

`package.json` 脚本：`dev`（Vite 开发）、`build`（产出 `web/dist/`）、`preview`、`lint`（ESLint）、`check`（Prettier 检查）、`format`、`test` / `test:watch`（Vitest）。构建需 Node.js 22+；后端以 `web/dist/` 为静态资源，不在运行时重建 UI。
