# Qingci-Bot

基于 Python 的 QQ 机器人框架，对接 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot)（OneBot 11 协议），支持 LLM 智能对话、Web UI 和桌面应用。

## 架构

```
┌──────────┐   OneBot 11 WS   ┌──────────────────────────────────────────┐   HTTP/WS   ┌──────────┐
│  LLBot   │ ◄──────────────► │            Qingci-Bot                   │ ◄─────────► │  Web UI  │
│ (协议层)  │  收发消息/事件    │  ┌──────────────────────────────────┐   │   API 推送   │  (管理端)  │
└──────────┘                  │  │ aiocqhttp (反向 WS 服务端)        │   │            └──────────┘
                              │  ├──────────────────────────────────┤   │
                              │  │ Dispatcher (Matcher/Rule/Perm)   │   │
                              │  ├──────────────────────────────────┤   │
                              │  │ PluginManager (热加载/双轨调度)    │   │
                              │  ├──────────────────────────────────┤   │
                              │  │ LLMManager (litellm 100+ 提供商)  │   │
                              │  ├──────────────────────────────────┤   │
                              │  │ Database (SQLModel + Alembic)    │   │
                              │  └──────────────────────────────────┘   │
                              └──────────────────────────────────────────┘
```

## 特性

- **OneBot 11 反向 WebSocket**：基于 [aiocqhttp](https://github.com/nonebot/aiocqhttp)，完整支持 OneBot v11 协议（消息段解析、API 调用、事件总线）
- **LLM 统一接口**：基于 [litellm](https://github.com/BerriAI/litellm)，支持 100+ 提供商（OpenAI / DeepSeek / Claude / Gemini / Ollama 等），含流式响应、Function Calling、多模态
- **会话上下文管理**：按群聊/用户独立维护对话历史，内存 + 数据库双写持久化，按条数与 Token 双重裁剪
- **插件系统**：借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持优先级、权限控制、命令注册器，向后兼容旧式 `on_message`
- **数据库 ORM**：SQLModel 模型定义 + Alembic 迁移管理，异步会话（aiosqlite + WAL 模式）
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

# 安装 Python 依赖（推荐用 uv，速度快）
uv pip install -e . --python .venv\Scripts\python.exe

# 或手动安装核心依赖
uv pip install fastapi "uvicorn[standard]" websockets aiocqhttp aiosqlite \
  sqlmodel alembic "sqlalchemy[asyncio]" litellm pydantic pyyaml httpx \
  --python .venv\Scripts\python.exe

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

- 地址：`ws://127.0.0.1:3001/ws`
- Access Token：留空（与 `config.yaml` 中 `onebot.access_token` 保持一致）

LLBot 会自动携带 OneBot v11 标准的 `X-Client-Role: universal` 和 `X-Self-ID` header 连接。

## 4. 配置 LLM

在 Web UI 的「LLM 配置」页面填写 API 信息，或直接编辑 `config.yaml`：

```yaml
llm:
  provider: deepseek              # openai / deepseek / ollama / claude / gemini / custom
  api_url: https://api.deepseek.com/v1  # 留空则按 provider 直连官方
  api_key: sk-your-key
  model: deepseek-chat
  system_prompt: 你是一个友好的 QQ 机器人助手。
  max_context_tokens: 8192        # 上下文窗口 token 上限，超出自动裁剪历史
```

**provider 路由规则**（基于 litellm）：
- `api_url` 非空：统一走 OpenAI 兼容协议（`openai/{model}` + `api_base`），兼容任意 OpenAI 协议服务
- `api_url` 为空：按 provider 直连官方（`deepseek/{model}`、`ollama/{model}` 等）
- `provider: custom`：必须填 `api_url`，走 OpenAI 兼容协议

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
  port: 3001                       # LLBot 连接 ws://host:port/ws
  access_token: ''
llm:
  provider: openai                 # openai / deepseek / ollama / claude / gemini / custom
  api_url: https://api.openai.com/v1  # 留空则按 provider 直连官方
  api_key: sk-xxx
  model: gpt-4o-mini
  max_tokens: 2048                 # 单次回复最大 token
  temperature: 0.7
  system_prompt: 你是一个友好的 QQ 机器人助手。
  max_history: 20                  # 最大对话历史轮数（每轮 = user + assistant）
  max_context_tokens: 8192         # 上下文窗口 token 上限，超出自动裁剪历史
api_key: ''                        # API 鉴权密钥
```

---

# 开发指南

## 项目结构

```
Qingci-Bot/
├── main.py                    # 统一入口
├── pyproject.toml
├── alembic.ini                # Alembic 迁移配置
├── config.yaml                # 配置文件（首次运行自动生成）
├── bot/
│   ├── config.py              # 配置管理（Pydantic 模型）
│   ├── core/
│   │   ├── bot.py             # Bot 主类（生命周期、事件调度）
│   │   ├── connection.py      # OneBot 连接（aiocqhttp 反向 WS）
│   │   ├── dispatcher.py      # 消息分发 + Matcher 调度
│   │   └── broadcast.py       # 消息广播
│   ├── llm/
│   │   ├── adapter.py         # LLM 适配器基类（支持 tools/images）
│   │   ├── litellm_adapter.py # litellm 实现（100+ 提供商）
│   │   └── manager.py         # 会话管理 + Token 裁剪 + 持久化
│   ├── db/
│   │   ├── database.py        # 数据库仓储（基于 SQLModel）
│   │   ├── engine.py          # 异步引擎 + 会话工厂（WAL 模式）
│   │   └── models.py          # SQLModel 模型定义
│   └── plugin/
│       ├── base.py            # 插件基类（支持 matchers 属性）
│       ├── manager.py         # 插件管理器（热加载 + 模块级收集）
│       ├── matcher.py         # Matcher + MatcherContext + 工厂函数
│       ├── rule.py            # 规则系统（startswith/command/regex 等）
│       ├── permission.py      # 权限系统（SUPERUSER/PRIVATE/GROUP 等）
│       └── builtin/           # 内置插件
│           ├── chat.py        # LLM 对话（Matcher API）
│           └── admin.py       # 管理命令（Matcher API）
├── migrations/                # Alembic 迁移脚本
│   ├── env.py                 # 异步迁移环境
│   └── versions/              # 迁移版本
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
    └── qingci-bot.db          # SQLite 数据库文件
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.12 + FastAPI + uvicorn |
| QQ 协议 | aiocqhttp (OneBot 11 反向 WS) |
| LLM | litellm (100+ 提供商统一接口) |
| 数据库 | SQLModel + Alembic + aiosqlite (WAL 模式) |
| 插件系统 | Matcher + Rule + Permission (借鉴 NoneBot2) |
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

Qingci-Bot 插件系统借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持两种开发方式：

- **新式（推荐）**：用 `on_command`/`on_message` 等装饰器注册 Matcher，配合 Rule 规则匹配和 Permission 权限控制
- **旧式（兼容）**：重写 `on_message` 方法，返回回复文本

两种方式可共存，Dispatcher 按 priority 优先调度 Matcher，无匹配时回退到旧式 `on_message`。

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
        # 新式：在这里注册 Matcher
        # 旧式：可做初始化
        ...

    async def on_unload(self):
        """插件卸载时调用（必须实现）"""
        ...

    # 旧式消息处理（新式插件可省略或返回 None）
    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        return None
```

### 注入的依赖

插件加载后，以下属性由 `PluginManager` 自动注入：

| 属性 | 类型 | 说明 |
|------|------|------|
| `self.bot` | `QingciBot` | Bot 主实例 |
| `self.db` | `Database` | SQLite 数据库（基于 SQLModel） |
| `self.config` | `ConfigManager` | 配置管理器（可读写 `config.yaml`） |
| `self.connection` | `OneBotConnection` | OneBot 连接（可调用 QQ API） |
| `self.llm` | `LLMManager` | LLM 管理器（基于 litellm） |
| `self.matchers` | `list[Matcher]` | Matcher 列表（在 `on_load` 中填充） |

### Matcher / Rule / Permission

**核心概念：**
- **Matcher**：绑定 handler + rule + permission + priority 的匹配单元
- **Rule**：消息匹配规则（前缀/命令/正则等），支持 `&`/`|`/`~` 组合
- **Permission**：权限检查（管理员/私聊/群聊等），支持 `&`/`|`/`~` 组合
- **MatcherContext**：增强版 MessageContext，额外注入 `bot`/`plugin`/`matcher` + `command`/`args`/`match`

**工厂函数：**

| 函数 | 说明 |
|------|------|
| `on_message(rule, permission, priority, block)` | 通用消息匹配器 |
| `on_command(cmd, rule, permission, priority, block)` | 命令匹配器（自动解析参数到 `ctx.args`） |
| `on_startswith(prefix, ...)` | 前缀匹配器 |
| `on_keyword(keywords, ...)` | 关键词匹配器 |
| `on_notice(rule, priority, block)` | 通知事件匹配器 |
| `on_request(rule, priority, block)` | 请求事件匹配器 |

**内置 Rule：** `startswith` / `endswith` / `fullmatch` / `contains` / `regex` / `command` / `to_me` / `is_private` / `is_group` / `keyword`

**内置 Permission：** `EVERYONE` / `SUPERUSER` / `ADMIN` / `PRIVATE` / `GROUP` / `MEMBER` / `USER(ids)` / `GROUP_MEMBER(ids)`

### MatcherContext 字段

继承自 `MessageContext`，额外字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ctx.bot` | `QingciBot` | Bot 实例（供模块级 handler 访问依赖） |
| `ctx.plugin` | `PluginBase` | 当前插件实例 |
| `ctx.matcher` | `Matcher` | 当前匹配器 |
| `ctx.command` | `str` | 匹配到的命令名（`command` 规则写入） |
| `ctx.args` | `str` | 命令参数 / 前缀后的剩余文本 |
| `ctx.match` | `re.Match` | 正则匹配结果（`regex` 规则写入） |

基础字段（同 MessageContext）：`raw_event` / `message_type` / `message_id` / `user_id` / `group_id` / `self_id` / `plain_text` / `raw_message` / `is_at_bot` / `at_list` / `images` / `sender`

### 注册方式

**方式一：插件内注册（推荐，可访问 self）**

```python
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_command, MatcherContext
from bot.plugin.permission import SUPERUSER

class MyPlugin(PluginBase):
    name = "my_plugin"

    async def on_load(self):
        # 注册 Matcher，handler 为 self 的方法
        self.matchers.append(
            on_command("ping", permission=SUPERUSER)(self._handle_ping)
        )

    async def _handle_ping(self, ctx: MatcherContext) -> str:
        return "pong!"

    async def on_unload(self):
        pass
```

**方式二：模块级装饰器（PluginManager 自动收集）**

```python
from bot.plugin.matcher import on_command, MatcherContext

@on_command("ping")
async def ping_handler(ctx: MatcherContext) -> str:
    return "pong"
```

### 示例一：命令匹配 + 权限控制

```python
# plugins/greet.py
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_command, MatcherContext
from bot.plugin.permission import SUPERUSER

class GreetPlugin(PluginBase):
    name = "greet"
    version = "1.0.0"
    description = "问候插件（管理员专属）"

    async def on_load(self):
        # /greet <名字> -> 你好，<名字>！
        self.matchers.append(
            on_command("greet", permission=SUPERUSER)(self._greet)
        )

    async def on_unload(self):
        pass

    async def _greet(self, ctx: MatcherContext) -> str:
        name = ctx.args.strip() or "朋友"
        return f"你好，{name}！"
```

### 示例二：前缀匹配 + Rule 组合

```python
# plugins/translator.py
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_startswith, MatcherContext
from bot.plugin.rule import is_group  # 仅群聊触发

class TranslatorPlugin(PluginBase):
    name = "translator"
    version = "1.0.0"
    description = "中英互译（前缀 翻译 触发，仅群聊）"

    async def on_load(self):
        self.matchers.append(
            on_startswith("翻译", rule=is_group())(self._translate)
        )

    async def on_unload(self):
        pass

    async def _translate(self, ctx: MatcherContext) -> str:
        text = ctx.args.strip()  # startswith 规则自动去除前缀，剩余文本存入 args
        if not text:
            return "请输入要翻译的内容，如：翻译 hello"

        reply = await self.llm.chat(
            message=f"请将以下内容翻译为{'中文' if any(c.isascii() for c in text) else '英文'}：{text}",
            message_type=ctx.message_type,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
        )
        return reply
```

### 示例三：正则匹配 + 关键词

```python
# plugins/reminder.py
import re
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_message, MatcherContext
from bot.plugin.rule import regex, keyword

class ReminderPlugin(PluginBase):
    name = "reminder"
    version = "1.0.0"
    description = "提醒插件（正则提取时间）"

    async def on_load(self):
        # 匹配 "提醒我 X 点 Y 分"
        self.matchers.append(
            on_message(rule=regex(r"提醒我.*?(\d+)点(\d+)分"))(self._set_reminder)
        )
        # 匹配包含 "提醒" 关键词
        self.matchers.append(
            on_message(rule=keyword("提醒"))(self._hint)
        )

    async def on_unload(self):
        pass

    async def _set_reminder(self, ctx: MatcherContext) -> str:
        hour = ctx.match.group(1)
        minute = ctx.match.group(2)
        return f"已设置 {hour}:{minute} 的提醒"

    async def _hint(self, ctx: MatcherContext) -> str:
        return "格式：提醒我 X 点 Y 分"
```

### 示例四：通知事件处理

```python
# plugins/welcome.py
from bot.plugin.base import PluginBase
from bot.plugin.matcher import on_notice, MatcherContext
from bot.plugin.rule import Rule

class WelcomePlugin(PluginBase):
    name = "welcome"
    version = "1.0.0"
    description = "新人入群欢迎"

    async def on_load(self):
        self.matchers.append(
            on_notice()(self._on_group_increase)
        )

    async def on_unload(self):
        pass

    async def _on_group_increase(self, ctx: MatcherContext) -> None:
        event = ctx.raw_event
        if event.get("notice_type") == "group_increase":
            group_id = event.get("group_id")
            user_id = event.get("user_id")
            await self.connection.call_api(
                "send_group_msg",
                {
                    "group_id": group_id,
                    "message": f"[CQ:at,qq={user_id}] 欢迎加入本群！",
                },
            )
```

### 示例五：旧式 on_message（向后兼容）

旧式插件无需改动，继续工作：

```python
# plugins/pingpong.py
from typing import Optional
from bot.plugin.base import PluginBase
from bot.core.dispatcher import MessageContext

class PingPongPlugin(PluginBase):
    name = "pingpong"
    version = "1.0.0"
    description = "Ping-Pong 响应"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass

    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        if ctx.plain_text == "ping":
            return "pong!"
        return None
```

### 调度顺序

1. Bot 收到消息 → Dispatcher 解析为 `MessageContext`
2. 收集所有 Matcher，按 `priority` 升序排序（越小越先执行）
3. 依次检查每个 Matcher 的 `permission` 和 `rule`
4. 匹配成功则执行 `handler`：
   - 返回非 `None`（回复文本）→ 发送回复，**停止整个分发链**
   - 返回 `None` + `block=True` → 停止后续 Matcher
   - 返回 `None` + `block=False` → 继续下一个 Matcher
5. 所有 Matcher 都未匹配 → 回退到旧式 `on_message`（跳过已注册 Matcher 的插件）

### 优先级与阻塞控制

```python
# priority 越小越先执行（默认 1）
# block=True（默认）匹配后停止后续 Matcher
# block=False 允许后续 Matcher 继续匹配

@on_command("ping", priority=1, block=True)
async def ping(ctx: MatcherContext) -> str:
    return "pong"

@on_message(rule=keyword("天气"), priority=10, block=False)
async def weather_log(ctx: MatcherContext) -> None:
    # 仅记录，不回复，不阻塞后续处理
    await self.db.save_message(...)
```

### 消息格式（CQ 码）

返回的回复文本支持 CQ 码：

| CQ 码 | 说明 |
|-------|------|
| `[CQ:at,qq=123456]` | @ 某人 |
| `[CQ:face,id=178]` | QQ 表情 |
| `[CQ:image,file=https://example.com/img.png]` | 图片 |
| `[CQ:reply,id=消息ID]` | 回复消息 |

```python
from bot.core.dispatcher import MessageDispatcher

at_code = MessageDispatcher.build_cq_at(ctx.user_id)
reply = f"{at_code} 收到！"
```

### 插件加载方式

**方式一：Web UI 加载（推荐）**

在「插件管理」页面输入模块路径（如 `plugins.pingpong`），点击加载。

**方式二：内置插件**

将插件文件放入 `bot/plugin/builtin/` 目录，Bot 启动时自动加载。

**方式三：外部目录**

将插件放在项目根目录的 `plugins/` 文件夹中，确保 `plugins/__init__.py` 存在，然后用模块路径 `plugins.xxx` 加载。

### 常用 OneBot API

通过 `self.connection.call_api(action, params)` 调用：

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

### 注意事项

- `on_load` 和 `on_unload` 是 `@abstractmethod`，**必须实现**（可以是 `pass`）
- 插件中不要使用阻塞操作（如 `time.sleep`），用 `asyncio.sleep` 代替
- `on_message` 返回空字符串 `""` 也会被当作回复发送，不需要回复时返回 `None`
- 插件可通过 `self.config` 修改配置，但需调用 `self.config.save()` 持久化
- 热重载会重新执行模块代码，类级别的可变状态会丢失
- 模块级装饰器注册的 Matcher 会自动关联到同模块的 PluginBase 子类

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
