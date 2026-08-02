# Qingci-Bot

基于 Python 的 QQ 机器人框架，对接 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot)（OneBot 11 协议），支持 LLM 智能对话、Web UI 和桌面应用。

## 架构

```
┌──────────┐   OneBot 11 WS   ┌──────────────────┐   HTTP/WS   ┌──────────┐
│  LLBot   │ ◄──────────────► │  Qingci-Bot     │ ◄─────────► │  Web UI  │
│ (协议层)  │  收发消息/事件    │  (Python 应用层)  │   API 推送   │  (管理端)  │
└──────────┘                  └──────────────────┘            └──────────┘
                                     │
                              ┌──────┴──────┐
                              │    LLM API  │
                              │ (OpenAI等)  │
                              └─────────────┘
```

## 特性

- **OneBot 11 反向 WebSocket**：LLBot 主动连接，部署简单
- **LLM 多模型支持**：OpenAI / DeepSeek / Ollama 等兼容 API
- **会话上下文管理**：按群聊/用户独立维护对话历史
- **插件系统**：内置聊天及管理插件，支持热加载外部插件
- **Web UI**：原神风格暗色主题，仪表盘 / LLM 配置 / 插件管理 / 消息日志 / 系统设置
- **桌面应用**：PyWebView 套壳 + 系统托盘，开机自启
- **离线可用**：前端资源本地打包，无外部 CDN 依赖

---

# 使用指南

## 环境要求

- Python 3.10+
- [LLBot](https://github.com/LLOneBot/LuckyLilliaBot)（QQ 协议端）

## 1. 安装

```bash
# 创建虚拟环境
uv venv --python python3.12 .venv

# 安装 Python 依赖
uv pip install fastapi "uvicorn[standard]" websockets aiosqlite pydantic pyyaml httpx --python .venv\Scripts\python.exe

# 安装桌面依赖（可选）
uv pip install pywebview pystray pillow --python .venv\Scripts\python.exe
```

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
```

启动后访问 `http://127.0.0.1:8080/ui` 进入管理界面。

## 3. 配置 LLBot

在 LLBot 中添加反向 WebSocket 连接：

- 地址：`ws://127.0.0.1:3001`
- Access Token：留空（与 `config.yaml` 中 `onebot.access_token` 保持一致）

## 4. 配置 LLM

在 Web UI 的「LLM 配置」页面填写 API 信息，或直接编辑 `config.yaml`：

```yaml
llm:
  provider: deepseek
  api_url: https://api.deepseek.com/v1
  api_key: sk-your-key
  model: deepseek-chat
  system_prompt: 你是一个友好的 QQ 机器人助手。
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--no-bot` | 仅启动 API 服务 | - |
| `--desktop` | 启动桌面应用 | - |
| `--port` | API 端口 | 8080 |
| `--host` | API 监听地址 | 127.0.0.1 |
| `--config` | 配置文件路径 | config.yaml |

## 管理命令

Bot 运行时，管理员可在群聊/私聊中发送以下命令：

| 命令 | 说明 |
|------|------|
| `/status` | 查看 Bot 运行状态 |
| `/clear` | 清除当前会话历史 |
| `/blacklist add <QQ>` | 添加用户到黑名单 |
| `/blacklist remove <QQ>` | 从黑名单移除用户 |

管理员 QQ 号在 `config.yaml` 的 `bot.admin_users` 中配置。

## API 鉴权

在 `config.yaml` 中设置 `api_key` 字段启用 API 鉴权：

```yaml
api_key: your-secret-key
```

- 为空时**不启用鉴权**（仅本地开发推荐）
- 设置后，所有写操作（启停 Bot、修改配置、插件管理）需要携带 `X-API-Key` 请求头
- 在 Web UI 的「系统设置」页面可同时配置服务端 Key 和浏览器端 Key

## 配置文件说明

```yaml
bot:
  name: Qingci-Bot
  admin_users: [123456789]        # 管理员 QQ 号列表
  trigger_mode: at                 # 触发方式: at / keyword / always
  trigger_keywords: ["/bot", "/ai"] # keyword 模式的触发词
  group_blacklist: []              # 群黑名单
  user_blacklist: []               # 用户黑名单
onebot:
  host: 127.0.0.1
  port: 3001
  access_token: ''
llm:
  provider: openai                 # openai / deepseek / ollama
  api_url: https://api.openai.com/v1
  api_key: sk-xxx
  model: gpt-4o-mini
  max_tokens: 2048
  temperature: 0.7
  system_prompt: 你是一个友好的 QQ 机器人助手。
  max_history: 20                  # 最大对话历史轮数
api_key: ''                        # API 鉴权密钥
```

---

# 开发指南

## 项目结构

```
Qingci-Bot/
├── main.py                    # 统一入口
├── pyproject.toml
├── config.yaml                # 配置文件（首次运行自动生成）
├── bot/
│   ├── config.py              # 配置管理
│   ├── core/
│   │   ├── bot.py             # Bot 主类
│   │   ├── connection.py      # OneBot WS 连接
│   │   ├── dispatcher.py      # 消息分发
│   │   └── broadcast.py       # 消息广播
│   ├── llm/
│   │   ├── adapter.py         # LLM 适配器基类
│   │   ├── openai.py          # OpenAI 兼容适配器
│   │   └── manager.py         # 多模型 + 会话管理
│   ├── db/
│   │   └── database.py        # SQLite 数据库
│   └── plugin/
│       ├── base.py            # 插件基类
│       ├── manager.py         # 插件管理器
│       └── builtin/           # 内置插件
│           ├── chat.py        # LLM 对话
│           └── admin.py       # 管理命令
├── api/
│   ├── auth.py                # API 鉴权
│   ├── server.py              # FastAPI 应用
│   └── routes/                # API 路由
├── web/                       # Vue 3 前端
│   └── src/
│       ├── views/             # 页面组件
│       ├── stores/            # Pinia 状态管理
│       ├── router/            # 路由配置
│       └── styles/            # 全局样式
├── desktop/
│   ├── main.py                # 桌面入口
│   └── tray.py                # 系统托盘
└── data/
    └── qingci-bot.db          # 数据库文件
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.12 + FastAPI + websockets |
| QQ 协议 | LLBot (OneBot 11) |
| LLM | httpx → OpenAI 兼容 API |
| 数据库 | SQLite (aiosqlite) |
| 前端 | Vue 3 + Vite + Pinia |
| 桌面 | PyWebView + pystray |

## 前端开发

```bash
cd web
npm install
npm run dev      # 开发模式（热更新，http://localhost:5173）
npm run build    # 构建生产版本到 web/dist/
```

开发模式下，前端请求会自动代理到 `http://127.0.0.1:8080`（在 `vite.config.js` 中配置）。

## 插件开发

Qingci-Bot 的插件系统基于 `PluginBase` 抽象基类，支持热加载/卸载/重载。插件可以处理消息、通知、请求三类事件，并能访问 Bot、数据库、LLM、OneBot 连接等核心组件。

### 插件基类

所有插件继承 `bot.plugin.base.PluginBase`：

```python
from bot.plugin.base import PluginBase

class MyPlugin(PluginBase):
    # 插件元信息（必填 name，其余可选）
    name = "my_plugin"
    version = "1.0.0"
    author = "YourName"
    description = "插件描述"

    async def on_load(self):
        """插件加载时调用（必须实现）"""
        ...

    async def on_unload(self):
        """插件卸载时调用（必须实现）"""
        ...

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        """处理消息，返回回复文本或 None"""
        return None

    async def on_notice(self, event: dict) -> None:
        """处理通知事件（群成员变动、戳一戳等）"""
        pass

    async def on_request(self, event: dict) -> Optional[bool]:
        """处理请求事件（加群/加好友）
        返回 True 同意，False 拒绝，None 忽略"""
        return None
```

### 注入的依赖

插件加载后，以下属性由 `PluginManager` 自动注入，可直接通过 `self` 访问：

| 属性 | 类型 | 说明 |
|------|------|------|
| `self.bot` | `QingciBot` | Bot 主实例 |
| `self.db` | `Database` | SQLite 数据库（可读写消息记录） |
| `self.config` | `ConfigManager` | 配置管理器（可读写 `config.yaml`） |
| `self.connection` | `OneBotConnection` | OneBot 连接（可调用 QQ API） |
| `self.llm` | `LLMManager` | LLM 管理器（可调用大模型对话） |

### MessageContext 字段

`on_message` 接收的 `ctx` 包含解析后的消息上下文：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ctx.raw_event` | `dict` | 原始 OneBot 事件 |
| `ctx.message_type` | `str` | `group` 或 `private` |
| `ctx.message_id` | `str` | 消息 ID |
| `ctx.user_id` | `int` | 发送者 QQ 号 |
| `ctx.group_id` | `int` | 群号（私聊为 0） |
| `ctx.self_id` | `int` | Bot 自己的 QQ 号 |
| `ctx.plain_text` | `str` | 纯文本内容（已去除 CQ 码） |
| `ctx.raw_message` | `str` | CQ 码原始文本 |
| `ctx.is_at_bot` | `bool` | 是否 @ 了 Bot |
| `ctx.at_list` | `list[int]` | 被 @ 的用户列表 |
| `ctx.images` | `list[str]` | 图片 URL 列表 |
| `ctx.sender` | `dict` | 发送者信息（昵称、角色等） |

### 示例一：Ping-Pong 插件

最简单的插件，收到 `ping` 回复 `pong`：

```python
# plugins/pingpong.py
from typing import Optional
from bot.plugin.base import PluginBase
from bot.core.dispatcher import MessageContext

class PingPongPlugin(PluginBase):
    name = "pingpong"
    version = "1.0.0"
    author = "YourName"
    description = "Ping-Pong 响应插件"

    async def on_load(self):
        print("[PingPong] 插件已加载")

    async def on_unload(self):
        print("[PingPong] 插件已卸载")

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        if ctx.plain_text == "ping":
            return "pong!"
        return None
```

### 示例二：调用 LLM 的插件

在插件中调用 LLM 进行对话，自动维护会话上下文：

```python
# plugins/translator.py
from typing import Optional
from bot.plugin.base import PluginBase
from bot.core.dispatcher import MessageContext

class TranslatorPlugin(PluginBase):
    name = "translator"
    version = "1.0.0"
    author = "YourName"
    description = "中英互译插件（前缀 翻译 触发）"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        if not ctx.plain_text.startswith("翻译 "):
            return None

        text = ctx.plain_text[3:].strip()
        if not text:
            return "请输入要翻译的内容，如：翻译 hello"

        # 调用 LLM（自动维护会话上下文）
        reply = await self.llm.chat(
            message=f"请将以下内容翻译为{'中文' if any(c.isascii() for c in text) else '英文'}：{text}",
            message_type=ctx.message_type,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
        )
        return reply
```

### 示例三：调用 OneBot API 的插件

通过 `self.connection.call_api()` 调用 OneBot 11 协议 API，发送图片、表情、戳一戳等：

```python
# plugins/poke_back.py
from typing import Optional
from bot.plugin.base import PluginBase
from bot.core.dispatcher import MessageContext

class PokeBackPlugin(PluginBase):
    name = "poke_back"
    version = "1.0.0"
    author = "YourName"
    description = "被戳自动回戳"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        return None  # 不回复消息

    async def on_notice(self, event: dict) -> None:
        # 戳一戳事件
        if event.get("notice_type") == "notify" and event.get("sub_type") == "poke":
            if event.get("target_id") == event.get("self_id"):
                # 被戳了，回戳
                user_id = event.get("user_id")
                group_id = event.get("group_id")
                if group_id:
                    await self.connection.call_api(
                        "group_poke",
                        {"group_id": group_id, "user_id": user_id},
                    )
```

常用 OneBot API：

| Action | 参数 | 说明 |
|--------|------|------|
| `send_msg` | `user_id` / `group_id`, `message` | 发送消息 |
| `send_group_msg` | `group_id`, `message` | 发送群消息 |
| `send_private_msg` | `user_id`, `message` | 发送私聊消息 |
| `delete_msg` | `message_id` | 撤回消息 |
| `get_group_member_list` | `group_id` | 获取群成员列表 |
| `get_group_member_info` | `group_id`, `user_id` | 获取群成员信息 |
| `set_group_kick` | `group_id`, `user_id` | 踢出群成员 |
| `set_group_ban` | `group_id`, `user_id`, `duration` | 禁言 |
| `group_poke` | `group_id`, `user_id` | 戳一戳 |

### 示例四：读写数据库的插件

通过 `self.db` 读写 SQLite 消息记录：

```python
# plugins/message_stats.py
from typing import Optional
from bot.plugin.base import PluginBase
from bot.core.dispatcher import MessageContext

class MessageStatsPlugin(PluginBase):
    name = "message_stats"
    version = "1.0.0"
    author = "YourName"
    description = "消息统计插件（发送 /stats 查看）"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        if ctx.plain_text != "/stats":
            return None

        # 搜索该用户最近消息
        messages = await self.db.search_messages(
            keyword="",
            user_id=ctx.user_id,
            limit=100,
        )
        return f"你最近发送了 {len(messages)} 条消息。"
```

### 示例五：读写配置的插件

通过 `self.config` 读写 `config.yaml`：

```python
# plugins/welcome.py
from typing import Optional
from bot.plugin.base import PluginBase
from bot.core.dispatcher import MessageContext

class WelcomePlugin(PluginBase):
    name = "welcome"
    version = "1.0.0"
    author = "YourName"
    description = "新人入群欢迎"

    # 自定义配置（直接存在 config.yaml 的 bot 字段下）
    welcome_text = "欢迎加入本群！"

    async def on_load(self):
        # 可以从配置中读取自定义字段
        bot_config = self.config.bot
        if hasattr(bot_config, "welcome_text"):
            self.welcome_text = bot_config.welcome_text

    async def on_unload(self):
        pass

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        return None

    async def on_notice(self, event: dict) -> None:
        if event.get("notice_type") == "group_increase":
            group_id = event.get("group_id")
            user_id = event.get("user_id")
            # 主动发送欢迎消息（不依赖消息回复）
            await self.connection.call_api(
                "send_group_msg",
                {
                    "group_id": group_id,
                    "message": f"[CQ:at,qq={user_id}] {self.welcome_text}",
                },
            )
```

### 消息格式（CQ 码）

返回的回复文本支持 CQ 码，常用的：

| CQ 码 | 说明 |
|-------|------|
| `[CQ:at,qq=123456]` | @ 某人 |
| `[CQ:face,id=178]` | QQ 表情 |
| `[CQ:image,file=https://example.com/img.png]` | 图片 |
| `[CQ:reply,id=消息ID]` | 回复消息 |

也可以使用 dispatcher 的辅助方法构建：

```python
from bot.core.dispatcher import MessageDispatcher

# 构建 CQ 码
at_code = MessageDispatcher.build_cq_at(ctx.user_id)
reply = f"{at_code} 收到！"
```

### 插件加载方式

**方式一：Web UI 加载（推荐）**

在「插件管理」页面输入模块路径（如 `plugins.pingpong`），点击加载。需要插件文件在 Python 导入路径中。

**方式二：内置插件**

将插件文件放入 `bot/plugin/builtin/` 目录，Bot 启动时自动加载。适合核心功能。

**方式三：外部目录**

将插件放在项目根目录的 `plugins/` 文件夹中，确保 `plugins/__init__.py` 存在，然后用模块路径 `plugins.xxx` 加载。

### 插件目录结构示例

```
Qingci-Bot/
├── plugins/                  # 外部插件目录
│   ├── __init__.py
│   ├── pingpong.py
│   ├── translator.py
│   └── welcome.py
├── bot/
│   └── plugin/
│       └── builtin/          # 内置插件目录
│           ├── chat.py
│           └── admin.py
└── ...
```

### 插件执行顺序

1. Bot 收到消息后，按插件加载顺序依次调用 `on_message`
2. 如果某插件返回非空字符串，**立即停止**后续插件的执行并发送回复
3. 如果所有插件都返回 `None`，消息不被回复
4. 内置插件（chat、admin）先于外部插件加载

### 注意事项

- `on_load` 和 `on_unload` 是 `@abstractmethod`，**必须实现**（可以是 `pass`）
- 插件中不要使用阻塞操作（如 `time.sleep`），使用 `asyncio.sleep` 代替
- `on_message` 返回空字符串 `""` 也会被当作回复发送，不需要回复时返回 `None`
- 插件可以通过 `self.config` 修改配置，但需调用 `self.config.save()` 持久化
- 热重载会重新执行模块代码，类级别的可变状态会丢失

## API 接口

所有接口前缀 `/api`，写操作需携带 `X-API-Key` 请求头（启用鉴权时）。

### Bot 控制 `/api/bot`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/status` | 否 | 获取 Bot 运行状态 |
| GET | `/health` | 否 | 健康检查 |
| POST | `/start` | 是 | 启动 Bot |
| POST | `/stop` | 是 | 停止 Bot |
| POST | `/restart` | 是 | 重启 Bot |

### 配置管理 `/api/config`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `` | 否 | 获取完整配置 |
| PUT | `` | 是 | 更新配置 |
| GET | `/bot` | 否 | 获取 Bot 配置 |
| PUT | `/bot` | 是 | 更新 Bot 配置 |
| GET | `/llm` | 否 | 获取 LLM 配置 |
| PUT | `/llm` | 是 | 更新 LLM 配置 |
| GET | `/onebot` | 否 | 获取 OneBot 配置 |
| POST | `/llm/test` | 是 | 测试 LLM 连接 |

### 插件管理 `/api/plugin`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `` | 否 | 获取插件列表 |
| GET | `/{name}` | 否 | 获取插件详情 |
| POST | `/{name}/reload` | 是 | 重载插件 |
| POST | `/load` | 是 | 加载外部插件 |
| DELETE | `/{name}` | 是 | 卸载插件 |

### 消息日志 `/api/log`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/messages` | 否 | 搜索消息记录 |
| GET | `/messages/count` | 否 | 获取消息总数 |
| DELETE | `/sessions` | 是 | 清除所有会话 |

### WebSocket `/api/ws/log`

实时推送消息记录，连接后自动接收新消息。

## 许可证

MIT
