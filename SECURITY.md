# Security Policy

## 支持的版本

| 版本   | 支持状态     |
| ------ | ------------ |
| 1.8.x  | 积极支持     |
| < 1.8  | 不再支持     |

## 报告漏洞

如果你发现安全漏洞，请**不要**在公开 Issue 中报告。

请通过以下方式私下报告：

1. 发送邮件至项目维护者
2. 或在 GitHub 主仓库（https://github.com/Qingci-Bot/Qingci-Bot-CE）提交私密 Security Advisory

请在报告中包含：

- 漏洞的详细描述
- 复现步骤
- 受影响版本
- 可能的修复建议（如有）

## 处理流程

1. 确认收到报告（24 小时内）
2. 评估漏洞严重性
3. 开发修复补丁
4. 发布安全更新
5. 公开披露（修复发布后 30 天）

## 安全最佳实践

### API Key 管理

- 请勿将 `config.yaml` 中的 `api_key` 提交到版本控制
- 定期更换 API Key
- 使用强随机密钥（建议 32 位以上）

### 网络部署

- 生产环境建议使用反向代理（Nginx/Caddy）提供 HTTPS
- 限制 API 监听地址（默认 `127.0.0.1`，仅本地访问）
- 如果暴露到公网，务必配置 `api_key`

### 依赖安全

- 定期更新依赖：`uv pip install --upgrade qingci-bot-ce qingci-plugin-sdk`
- 关注 [GitHub Advisory Database](https://github.com/advisories) 中的相关漏洞

### 插件安全

- 只安装可信来源的插件（Web UI 插件市场或 `PluginManager.install()` 支持 git 仓库 / HTTP 归档 / 本地目录）
- 插件声明的第三方依赖（`requirements.txt`）自动安装到实例隔离目录，生产环境如不需要可关闭 `bot.auto_install_plugin_deps`

## 致谢

感谢所有负责任地报告安全问题的研究人员和用户。