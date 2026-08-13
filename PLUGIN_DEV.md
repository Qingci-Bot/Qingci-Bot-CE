# Qingci-Bot CE 插件开发指南

## 前端开发

```bash
cd web
npm install
npm run dev      # 开发模式（热更新，http://localhost:5173）
npm run build    # 构建生产版本到 web/dist/
```

开发模式下，前端请求会自动代理到 `http://127.0.0.1:8080`（在 `vite.config.js` 中配置）。

## Web UI 页面

| 页面 | 路由 | 说明 |
|------|------|------|
| 仪表盘 | `/` | 运行状态卡片 + LLM 用量统计图表（依赖 `log.usage_tracking`） |
| LLM 配置 | `/config` | 提供商切换（联动 api_url/model）、模型列表拉取、人格管理、MCP 服务器配置、连接测试 |
| 对话调试台 | `/lab` | 无需进入 QQ 即可流式测试 LLM 回复；独立会话 key，不污染真实对话（走 `/api/ws/chat`） |
| 群配置 | `/groups` | 各群 Bot 开关与触发模式 |
| 插件管理 | `/plugins` | 插件列表 / 详情 / 重载 / 加载外部插件 / 卸载 / 禁用 / 启用 |
| 消息日志 | `/logs` | 实时消息流 + 会话记录可视化（按会话分组查看/删除，支持清理与 CSV 导出） |
| 系统设置 | `/settings` | 服务端/浏览器 API Key 配置 |
| 登录 | `/login` | API Key 登录（服务端已配置 `api_key` 时显示） |

---

## 插件开发

> **快速开始**：复制 `plugins/_template/` 目录为 `plugins/my_plugin/` 即可开始开发。
> 模板目录涵盖所有功能（命令/前缀/关键词/通知/请求/定时任务/Function Calling），附详细中文注释。
> 最小示例见 `plugins/hello/`（15 行代码，开箱即用）。

### 命名规范

| 项目 | 规范 | 示例 |
|------|------|------|
| 目录名 | 小写英文 + 下划线，与 `name` 一致，**不能以 `_` 开头** | `chat/`、`my_plugin/` |
| 入口文件 | 目录下的 `__init__.py` | `chat/__init__.py` |
| 类名 | `{Name}Plugin` 帕斯卡命名 | `ChatPlugin`、`HelloPlugin` |
| `name` 属性 | 小写英文 + 下划线，插件唯一标识，**必填** | `"chat"`、`"my_plugin"` |

**硬性约束：**
- 目录名以 `_` 开头（如 `_template/`）会被跳过，不会加载
- 每个插件包只能定义 **1 个** `PluginBase` 子类，多个会报错
- 插件类必须定义在 `__init__.py` 模块内，不能从其他模块 `import` 进来
- `name` 不能与其他已加载插件重名
- 单文件 `.py` 插件仍兼容，但推荐使用目录结构

---

Qingci-Bot CE 插件系统借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持两种开发方式：

- **新式（推荐）**：用 `on_command`/`on_message` 等装饰器注册 Matcher，配合 Rule 规则匹配和 Permission 权限控制
- **旧式（兼容）**：重写 `on_message` 方法，返回回复文本

两种方式可共存，Dispatcher 按 priority 优先调度 Matcher，无匹配时回退到旧式 `on_message`。

### 插件基类

所有插件继承 `bot.plugin.base.PluginBase`：

```python
from bot.plugin.base import PluginBase, PluginStatus

class MyPlugin(PluginBase):
    # 插件元信息（必填 name，其余可选）
    name = "my_plugin"
    version = "1.0.0"
    author = "YourName"
    description = "插件描述"
    category = "tool"       # 插件分类：chat / admin / tool / fun / 自定义
    require = []            # 依赖的其他插件 name 列表，支持 PEP 440 版本约束（如 "chat>=1.0,<2.0"）

    async def on_load(self):
        """插件加载时调用（必须实现）"""
        # 新式：在这里注册 Matcher
        # 旧式：可做初始化
        ...

    async def on_unload(self):
        """插件卸载时调用（必须实现）"""
        ...

    async def on_disable(self):
        """插件被禁用时调用（可选，用于停用定时任务等轻量清理）"""
        pass

    async def on_enable(self):
        """插件被启用时调用（可选，用于恢复定时任务等）"""
        pass

    # 旧式消息处理（新式插件可省略或返回 None）
    async def on_message(self, ctx: MessageContext) -> Optional[str]:
        return None
```

### 插件状态（PluginStatus）

插件状态由 `PluginStatus` 枚举管理，替代原 `enabled` 布尔值：

| 状态 | 值 | 说明 |
|------|------|------|
| `LOADING` | `"loading"` | 正在加载（on_load 执行中） |
| `LOADED` | `"loaded"` | 已加载，正常运行 |
| `DISABLED` | `"disabled"` | 已禁用，跳过事件分发 |
| `ERROR` | `"error"` | 加载/运行出错 |
| `UNLOADING` | `"unloading"` | 正在卸载（on_unload 执行中） |

```python
# 状态属性（只读）
plugin.status        # PluginStatus.LOADED
plugin.enabled       # bool，向后兼容：LOADING/LOADED 为 True
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
| `self.scheduler` | `BotScheduler` | 定时任务调度器 |
| `self.tool_registry` | `ToolRegistry` | Function Calling 工具注册表 |
| `self.knowledge_store` | `KnowledgeStore` | 知识库（RAG 未启用时为 None） |
| `self.session_state` | `SessionStateManager` | 会话状态（TTL 键值存储） |
| `self.matchers` | `list[Matcher]` | Matcher 列表（在 `on_load` 中填充） |

### 依赖注入容器（DI Container）

框架内置轻量级 DI 容器 `bot.di`，按类型自动注入服务。插件只需声明类型注解即可：

```python
from bot.core.session_state import SessionStateManager

class MyPlugin(PluginBase):
    name = "my_plugin"
    # 声明类型注解后，框架自动注入
    session_state: SessionStateManager
    # 也支持 Optional[X] 类型
    # db: Optional[Database] = None
    # llm: Optional[LLMManager] = None
```

`PluginManager._init_plugin` 会先调用 `await bot.di.inject(plugin)` 按类型自动注入，再手动赋值兜底保证兼容。

**注入特性：**
- 支持 `Optional[X]` 类型注解（自动提取内部类型）
- 不会覆盖已设置的非 None 属性值
- 支持 `register_as(InterfaceType, instance)` 接口绑定

**生命周期：**
| 生命周期 | 说明 |
|---------|------|
| `SINGLETON` | 全局唯一实例（默认） |
| `TRANSIENT` | 每次 resolve 创建新实例 |
| `SCOPED` | 同一 scope 内返回同一实例 |

### 会话状态（SessionState / TTL 键值存储）

借鉴 NoneBot2 的 `session.state`，提供带过期时间的会话级临时键值存储，适用于多步骤对话、表单填写、等待确认等场景。

**在 handler 中通过 `ctx.session_state` 使用：**

```python
async def _handle_register(self, ctx: MatcherContext) -> str:
    step = ctx.session_state.get("step", "start")

    if step == "start":
        ctx.session_state.set("step", "waiting_name", ttl=300)
        return "请输入你的名字："

    if step == "waiting_name":
        ctx.session_state.set("name", ctx.plain_text, ttl=300)
        ctx.session_state.set("step", "waiting_age", ttl=300)
        return f"你好 {ctx.plain_text}，请输入你的年龄："

    if step == "waiting_age":
        name = ctx.session_state.get("name")
        return f"注册完成！{name}，{ctx.plain_text}岁"
```

**在插件中通过 `self.session_state` 使用：**

```python
# 按用户/群聊隔离
self.session_state.set("last_command", "ping", user_id=123, ttl=60)
last = self.session_state.get("last_command", user_id=123)

# 群聊共享状态
self.session_state.set("banned_keywords", ["广告"], group_id=456)
```

**会话键规则：**
| 场景 | 会话键 |
|------|--------|
| 私聊 | `private:{user_id}` |
| 群聊+用户 | `group:{group_id}:{user_id}` |
| 群聊共享 | `group:{group_id}` |
| 自定义 | `custom_key` 参数 |

**API 速查：**
| 方法 | 说明 |
|------|------|
| `get(key, default)` | 获取值，过期自动删除 |
| `set(key, value, ttl=0)` | 设置值，ttl=0 永不过期 |
| `pop(key, default)` | 获取并删除键 |
| `expire(key, ttl)` | 为已有键设置过期时间 |
| `ttl(key)` | 获取键剩余过期时间（秒） |
| `delete(key)` | 删除键 |
| `clear()` | 清空当前会话状态 |
| `keys()` | 返回所有有效键 |
| `items()` | 返回所有有效键值对 |
| `count()` | 获取有效键数量 |

**SessionStateManager 全局操作（需 await）：**
| 方法 | 说明 |
|------|------|
| `await stats()` | 获取统计信息（会话数、键数） |
| `await remove_session(user_id=...)` | 显式删除会话 |
| `await serialize()` | 序列化所有会话状态 |
| `await deserialize(data)` | 从序列化数据恢复 |

### 插件依赖（require）

插件可声明依赖的其他插件，加载前 `PluginManager` 会自动先加载依赖：

```python
from bot.plugin.base import PluginBase

class MyPlugin(PluginBase):
    name = "my_plugin"
    require = ["admin"]   # 依赖 admin 插件（内置插件名或已加载插件名）

    async def on_load(self):
        # 通过 bot 获取依赖插件实例，调用其公开方法
        admin = self.bot.plugin_manager.get("admin")
        ...
```

- 依赖已注册则跳过；未注册时尝试加载 `bot.plugin.builtin.<name>` 模块
- 依赖缺失或形成循环依赖时插件加载失败（报错并保持旧插件生效）

**PEP 440 版本约束：**

```python
require = ["chat>=1.0,<2.0"]   # 依赖 chat 插件 1.x 版本
require = ["admin>=1.1"]        # 依赖 admin 插件 1.1 及以上
require = ["knowledge"]         # 无版本约束
```

### 插件级配置（plugin_config）

插件可通过定义 `Config` 内嵌类声明配置项，框架自动从 `config.yaml` 的 `plugins.<name>` 节加载：

```python
from pydantic import BaseModel

class MyPlugin(PluginBase):
    name = "my_plugin"

    class Config(BaseModel):
        greeting: str = "你好"
        max_length: int = 100

    async def on_load(self):
        # self.plugin_config 已自动加载，类型为 Config 实例
        greeting = self.plugin_config.greeting
        ...
```

`config.yaml` 对应配置：

```yaml
plugins:
  my_plugin:
    greeting: "Hello"
    max_length: 200
```

### 插件导出/导入（export / require）

插件间可通过 `export()` / `get_exports()` 暴露和调用服务接口：

```python
# 提供方（chat 插件）
class ChatPlugin(PluginBase):
    name = "chat"
    async def on_load(self):
        self.export("get_history", self.get_history)
        self.export("clear_history", self.clear_history)

# 消费方（依赖 chat 插件）
class MyPlugin(PluginBase):
    name = "my_plugin"
    require = ["chat"]
    async def on_load(self):
        chat = self.get_exports("chat")  # 获取导出字典
        history = await chat["get_history"](user_id=123)
```

> 注意：获取导出使用 `get_exports()` 方法，`require` 仅作为类属性声明依赖（两者原本重名，已拆分）。

### 插件级中间件（register_before / register_after）

每个插件可注册 handler 前置/后置钩子，拦截或修改 handler 返回值：

```python
class MyPlugin(PluginBase):
    name = "my_plugin"

    async def on_load(self):
        # 前置钩子：返回非 None 时拦截，跳过 handler
        self.register_before(self._before_handler)
        # 后置钩子：可修改 handler 返回值
        self.register_after(self._after_handler)

    async def _before_handler(self, matcher, ctx):
        # 记录日志、限流检查等
        return None  # None 表示不拦截

    async def _after_handler(self, matcher, ctx, result):
        # 追加签名、脱敏过滤等
        return result
```

### 插件元数据发现（plugin.json）

在插件目录下放置 `plugin.json` 文件，无需导入模块即可发现插件元信息：

```json
{
  "name": "my_plugin",
  "version": "1.0.0",
  "author": "YourName",
  "description": "插件描述",
  "category": "tool",
  "require": ["chat>=1.0"]
}
```

框架提供 `GET /api/plugin/discover/metadata` API 扫描所有 `plugin.json`。

### Web 管理页面（register_page）

插件可注册自带的 Web 管理页面，入口自动显示在「插件管理」页面的插件卡片上，点击后右侧滑出抽屉 iframe 加载。

```python
class MyPlugin(PluginBase):
    name = "my_plugin"

    async def on_load(self):
        # 注册管理页面（static_dir 可选，默认自动探测插件目录下的 web/ 子目录）
        self.register_page("群排行", icon="📊", static_dir="/path/to/web/dist")
        self.register_page("成员管理", icon="👤")
```

**参数说明：**
| 参数 | 类型 | 说明 |
|------|------|------|
| `title` | `str` | 页面标题，显示在按钮上 |
| `icon` | `str` | 图标字符，可选，默认 `◇` |
| `static_dir` | `str` | 静态文件目录的绝对路径，可选。省略时自动探测插件 `__init__.py` 同级的 `web/` 目录 |

**推荐目录结构：**
```
plugins/my_plugin/
├── __init__.py
├── plugin.json
└── web/               # ← register_page 自动探测此目录
    ├── index.html      # 入口页面
    ├── style.css
    └── app.js
```

**静态文件挂载：** 框架自动将 `web/` 目录挂载到 `/api/plugin-data/{plugin_name}/`，前端通过 iframe 加载。插件页面需预构建为纯静态 HTML/CSS/JS，不依赖框架前端构建链。

### Matcher / Rule / Permission

**核心概念：**
- **Matcher**：绑定 handler + rule + permission + priority 的匹配单元
- **Rule**：消息匹配规则（前缀/命令/正则等），支持 `&`/`|`/`~` 组合
- **Permission**：权限检查（管理员/私聊/群聊等），支持 `&`/`|`/`~` 组合
- **MatcherContext**：增强版 MessageContext，额外注入 `bot`/`plugin`/`matcher` + `command`/`args`/`match`

**工厂函数：**

| 函数 | 说明 |
|------|------|
| `on_message(rule, permission, priority, block, temp)` | 通用消息匹配器 |
| `on_command(cmd, rule, permission, priority, block, temp)` | 命令匹配器（自动解析参数到 `ctx.args`） |
| `on_startswith(prefix, ...)` | 前缀匹配器 |
| `on_keyword(keywords, ...)` | 关键词匹配器 |
| `on_notice(rule, priority, block, temp)` | 通知事件匹配器 |
| `on_request(rule, priority, block, temp)` | 请求事件匹配器 |

**一次性匹配器（temp=True）**：匹配执行后自动从所属插件移除，适用于"等待下一次对话"等只应触发一次的场景，例如"输入数字确认操作"：

```python
self.matchers.append(
    on_command("confirm", temp=True)(self._confirm)
)
```

**内置 Rule：** `startswith` / `endswith` / `fullmatch` / `contains` / `regex` / `command` / `to_me` / `is_private` / `is_group` / `keyword` / `rate_limit`

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

### 插件测试工具（bot.testing）

框架内置 `bot.testing` 包，无需启动真实 Bot 即可用 pytest 模拟消息事件、加载插件、断言回复。插件作者可在自己项目中编写：

```python
# tests/test_my_plugin.py
import pytest
from bot.testing import TestBot, private_message, group_message

@pytest.fixture
def bot():
    return TestBot()  # 轻量测试环境（默认 10001 为管理员）

async def test_ping(bot):
    await bot.load_plugin("my_plugin")  # 模块路径，须可 import
    reply = await bot.send(private_message("/ping"))
    assert reply == "pong"

async def test_group_and_permission(bot):
    await bot.load_plugin("my_plugin")
    reply = await bot.send(group_message("/admin", user_id=99999, group_id=20001))
    assert reply is None  # 非管理员被权限拦截
```

**TestBot 常用 API：**

| 方法/属性 | 说明 |
|---------|------|
| `await load_plugin(module_path)` | 加载插件模块（完整链路：依赖解析 + on_load） |
| `await send(event)` | 发送事件，返回 Bot 回复（str），无回复返回 None |
| `await send_private(text, user_id=10001)` | 发送私聊消息 |
| `await send_group(text, user_id, group_id)` | 发送群聊消息 |
| `sent_messages` | 插件主动发送的所有消息 `[(type, target, text)]` |
| `api_calls` | 插件调用过的所有 OneBot API `[(action, params)]` |
| `get_plugin(name)` | 获取已加载插件实例 |
| `await cleanup()` | 卸载插件并清空会话状态 |

**事件构造器：**

| 函数 | 说明 |
|------|------|
| `private_message(text, user_id=10001, at_bot=False)` | 私聊消息 |
| `group_message(text, user_id=10001, group_id=20001, at_bot=False)` | 群聊消息 |
| `make_message_event(text, ...)` | 通用消息事件（支持 images、sender 等） |
| `make_notice_event(notice_type, ...)` | 通知事件（如 `group_increase`） |
| `make_request_event(request_type, ...)` | 请求事件（如 `friend` / `group`） |

**特性：**
- 完整走 Dispatcher 调度链路（Matcher + 旧式 `on_message` 回退），与生产行为一致
- 会话状态（`ctx.session_state`）、依赖注入、插件级配置均可用
- 默认 `FakeConfig` 的 `admin_users=[10001]`，可传入自定义 config 覆盖
- 插件主动发送的消息记录在 `sent_messages`，便于断言

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

> **兼容性说明**：此前实现与文档不一致（实际为大者优先），本次已修正为与文档一致的升序匹配。
> 内置插件中 admin（priority=1）先于 chat（priority=50）执行。
> 外部插件开发者若此前依赖旧的"大者优先"实际顺序，请自查 priority 配置。

### 消息构造（类型化 Message / CQ 码）

**推荐使用类型化消息构造器**（`bot/core/message.py`），自动处理 CQ 码转义：

```python
from bot.core.message import Message, MessageSegment

# 回复 + @ + 文本 + 图片 组合
msg = Message(
    MessageSegment.reply(ctx.message_id),
    MessageSegment.at(ctx.user_id),
    MessageSegment.text("请看这张图："),
    MessageSegment.image("https://example.com/img.png"),
)
await self.connection.send_msg("group", ctx.group_id, str(msg))
```

支持的消息段：`text` / `at` / `at_all` / `image` / `face` / `voice` / `video` / `reply` / `forward`。`Message.extract_plain_text()` 可提取纯文本。

**手动 CQ 码**（简单场景仍可用）：

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

### 全局事件钩子（消息中间件）

Bot 提供全局前置 / 后置钩子，用于横切统计、审计、预处理，建议在插件 `on_load` 中注册：

```python
# 前置钩子：async (event, ctx) -> Optional[str]
# 返回非 None 时拦截该事件，返回值作为回复发送并终止分发
async def pre_hook(event, ctx):
    return None

# 后置钩子：async (event, ctx, reply) -> None
# 在消息回复发送后触发（reply 为最终回复或 None）
async def post_hook(event, ctx, reply):
    pass

self.bot.register_pre_hook(pre_hook)
self.bot.register_post_hook(post_hook)
```

钩子异常隔离（不影响主链路），注册自动去重。

### 插件加载方式

**方式一：外部目录自动加载（推荐）**

将插件包（目录）放入项目根目录的 `plugins/` 文件夹中，Bot 启动时自动扫描加载。无需手动操作，源码运行和 exe 打包均支持。

```
plugins/
├── __init__.py        # 包标记（自动创建）
├── _template/         # 完整模板（以 _ 开头，不会被加载）
│   ├── __init__.py
│   └── plugin.json
├── hello/             # 最小示例
│   └── __init__.py
└── my_plugin/         # 你的插件 → 自动加载
    ├── __init__.py    # 插件入口（必需）
    ├── plugin.json    # 元数据（可选）
    └── web/           # Web 管理页面（可选）
        └── index.html
```

> 以 `_` 开头的目录（如 `_template/`）不会被自动加载，可放心保留模板。
> 单文件 `.py` 插件仍兼容，但同名时目录型优先于文件型。

**方式二：Web UI 加载**

在「插件管理」页面输入模块路径（如 `plugins.my_plugin`），点击加载。

**方式三：内置插件**

将插件文件放入 `bot/plugin/builtin/` 目录，Bot 启动时自动加载。

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
- `on_disable` 和 `on_enable` 是可选钩子：禁用/启用不触发 `on_load`/`on_unload`，仅做轻量清理（如停用/恢复定时任务）
- 插件被禁用后，实例保留在内存中，Matcher 和旧式回调均不触发，API 返回的 `enabled` 字段反映当前状态
- 插件中不要使用阻塞操作（如 `time.sleep`），用 `asyncio.sleep` 代替
- `on_message` 返回空字符串 `""` 也会被当作回复发送，不需要回复时返回 `None`
- 插件可通过 `self.config` 修改配置，但需调用 `self.config.save()` 持久化
- 热重载会重新执行模块代码，类级别的可变状态会丢失
- 模块级装饰器注册的 Matcher 会自动关联到同模块的 PluginBase 子类
- 插件重载采用"先建后拆"：新版本加载成功前旧插件保持生效；新版本加载失败时旧插件继续工作，不会出现插件真空
- 重载后的模块若不再定义插件类，重载接口会返回失败（而非静默成功），旧插件保持生效

---

## API 接口

所有接口前缀 `/api`。启用鉴权（配置 `api_key`）时，除 `/api/bot/status`、`/api/bot/health`、`/api/auth/*` 外均需携带 `X-API-Key` 请求头；`api_key` 为空时全部免鉴权。

**错误响应与超时说明：**

- `/api/bot/start`、`/stop`、`/restart` 有超时保护（启动 30s / 停止 15s），超时返回 `504` 并自动尝试清理残留资源，可通过 `/api/bot/status` 确认实际状态
- `/api/plugin/load` 请求体必须为 JSON 且包含字符串字段 `module_path`，类型非法时返回 `422`
- 所有 `5xx` 错误的 `detail` 为通用文案（不暴露内部异常细节），详细原因见服务端日志

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
| GET | `` | 是 | 获取完整配置（敏感字段脱敏为 `***`） |
| PUT | `` | 是 | 更新配置（深度合并，`***` 占位符自动过滤） |
| GET | `/bot` | 是 | 获取 Bot 配置 |
| PUT | `/bot` | 是 | 更新 Bot 配置 |
| GET | `/llm` | 是 | 获取 LLM 配置 |
| PUT | `/llm` | 是 | 更新 LLM 配置（provider=custom 时强制校验 api_url） |
| GET | `/llm/presets` | 是 | 获取 LLM 提供商预设（api_url + 推荐 model，切换 provider 自动联动） |
| POST | `/llm/models` | 是 | 查询提供商可用模型列表（按 provider 调用对应 API，10s 超时，失败 400 透传原因） |
| GET | `/onebot` | 是 | 获取 OneBot 配置 |
| POST | `/llm/test` | 是 | 测试 LLM 连接（返回 `{available, message}`） |

### 插件管理 `/api/plugin`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `` | 是 | 获取插件列表（含状态、分类、Web 管理页面入口） |
| GET | `/{name}` | 是 | 获取插件详情 |
| POST | `/{name}/reload` | 是 | 重载插件 |
| POST | `/load` | 是 | 加载外部插件（仅允许 `plugins.*` / `bot.plugin.builtin.*` 白名单前缀） |
| DELETE | `/{name}` | 是 | 卸载插件（内置插件 chat/admin/help/imagegen/knowledge 不可卸载） |
| POST | `/{name}/disable` | 是 | 禁用插件（保留实例，跳过事件分发） |
| POST | `/{name}/enable` | 是 | 启用插件（恢复事件分发） |
| GET | `/{name}/metrics` | 是 | 获取插件执行指标（调用次数、平均耗时、错误率） |
| GET | `/discover/metadata` | 是 | 无导入发现：扫描 plugins/ 目录中的 plugin.json 元数据 |

### 消息日志与用量 `/api/log`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/messages` | 是 | 搜索消息记录 |
| GET | `/messages/count` | 是 | 获取消息总数 |
| GET | `/messages/export` | 是 | 导出消息记录（CSV 流式，utf-8-sig，Excel 直接打开不乱码） |
| GET | `/usage` | 是 | LLM 用量统计（依赖 `log.usage_tracking`） |
| DELETE | `/messages` | 是 | 删除消息记录；支持 `user_id` / `group_id` / `before_days` 过滤，全部删除需显式 `confirm=true` |
| DELETE | `/sessions` | 是 | 清除所有会话（需 `confirm=true`） |
| GET | `/sessions` | 是 | 会话列表（按最后活跃排序，含条数与归属 QQ） |
| GET | `/sessions/messages` | 是 | 查看指定会话历史（`?key=private:10001` 或 `group:10001:20002`） |
| DELETE | `/sessions/one` | 是 | 删除指定会话（`?key=会话key`，带审计） |

### 群配置 `/api/group`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/list` | 是 | 群配置列表 |
| GET | `/{group_id}` | 是 | 获取单群配置 |
| PUT | `/{group_id}` | 是 | 更新群配置（Bot 开关等） |

### 登录与鉴权 `/api/auth`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/status` | 否 | 是否需要登录（配置 api_key 是否非空） |
| POST | `/login` | 否 | 登录；按来源 IP 防暴力限流，连续失败 5 次后冷却 60 秒返回 429 |

### 数据库备份 `/api/backup`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/db` | 是 | 在线备份到 `data/backups/`（sqlite backup API，文件名带随机后缀，保留最近 10 份） |

### 审计日志 `/api/audit`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/logs` | 是 | 审计日志倒序查询（配置变更 / 启停 / 登录 / 备份等） |

### WebSocket

| 路径 | 鉴权 | 说明 |
|------|------|------|
| `/api/ws/log` | `token` 查询参数 | 实时推送消息记录，连接后自动接收新消息；60s 心跳保活（90s 无消息断开），连接数上限 32 |
| `/api/ws/chat` | `token` 查询参数 | 对话调试台：客户端发送 `{"message": "...", "user_id": 900000001}`，服务端逐块返回 `{"type":"delta","text":...}`，结束返回 `{"type":"done"}`；流式调用 LLM，独立连接池（上限 32） |

---

## 打包为 exe

使用 PyInstaller 将 Qingci-Bot CE 打包为 Windows 可执行程序（onedir 模式）。

### 构建

```powershell
# 依赖：PyInstaller 已安装在 .venv（uv pip install pyinstaller）
# Web UI 需先构建（web\dist 存在时可跳过）
cd web; npm install; npm run build; cd ..

# 一键打包
.\build.ps1
```

> `config.yaml` 已被 `.gitignore` 忽略（避免密钥入库）。新克隆的仓库中没有该文件，打包前需先从 `config.example.yaml` 复制一份并填入配置：
>
> ```powershell
> Copy-Item config.example.yaml config.yaml
> ```

产物位于 `dist\qingci-bot\`：

```
dist\qingci-bot\
├── qingci-bot.exe        # 主程序（带控制台，日志直接可见）
├── _internal\            # Python 运行时与依赖（勿动）
├── web\dist\             # Web UI 静态资源（build.ps1 复制）
├── config.yaml           # 配置文件（build.ps1 从项目根复制/暂存还原）
└── data\                 # SQLite 数据库 / 备份 / 敏感词库
```

### 运行

```powershell
.\dist\qingci-bot\qingci-bot.exe              # Bot + API 服务
.\dist\qingci-bot\qingci-bot.exe --no-bot     # 仅 API / Web UI
.\dist\qingci-bot\qingci-bot.exe --port 9000  # 指定端口
```

启动后访问 `http://127.0.0.1:8080/ui/`。

> **启动性能**：litellm 采用延迟导入，启动阶段不会加载该重型依赖（节省约 3.5 秒），仅首次真正调用 LLM 时一次性导入；首次运行 `config.yaml` 缺失时自动生成默认配置，无需手工准备。

### 注意事项

- `config.yaml` 与 `data\` 按 **exe 所在目录** 相对定位：分发时整个 `dist\qingci-bot\` 目录一起拷贝，勿单独移动 exe。
- 首次运行若缺少数据库会自动建表（SQLModel create_all）；`config.yaml` 缺失时会自动生成默认配置。
- 重新执行 `build.ps1` 不会覆盖产物目录中已有的 `config.yaml` 与 `data\`（脚本会先暂存后还原）。
- `--desktop` 桌面模式依赖系统 WebView2 运行时（pywebview EdgeChromium 后端），未安装的系统可能无法打开窗口。
- 如需无控制台窗口模式，将 `qingci-bot.spec` 中 `console=True` 改为 `False` 后重新构建。