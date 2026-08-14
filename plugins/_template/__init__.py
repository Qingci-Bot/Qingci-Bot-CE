"""Qingci-Bot CE 插件开发模板（完整版）

将 plugins/_template/ 目录复制为 plugins/your_plugin/ 即可开始开发。
以 _ 开头的目录不会被自动加载，可放心保留此模板。

推荐目录结构：
  plugins/your_plugin/
    ├── __init__.py      # 插件入口（必需）
    ├── plugin.json       # 元数据（可选，替代类属性）
    └── web/              # Web 管理页面（可选）
        └── index.html

====== 快速上手 ======
1. 复制目录: cp -r plugins/_template plugins/my_plugin
2. 修改类名和 name 属性
3. 在 on_load 中注册 Matcher
4. 重启 Bot 或通过 Web UI 插件管理热重载

====== Matcher 类型 ======
- on_command:    命令触发  "/ping" -> 自动解析命令名和参数
- on_startswith: 前缀触发  "天气 北京" -> ctx.args = "北京"
- on_keyword:    关键词触发  消息含"帮助"即触发
- on_message:    所有消息触发（需配合 Rule 过滤）
- on_notice:     通知事件（群成员增加/减少等）
- on_request:    请求事件（加好友/加群申请）

====== 依赖注入（在 on_load 中通过 self 访问）======
self.bot          - Bot 主实例
self.db           - 数据库（异步 API）
self.config       - 配置管理器
self.connection   - OneBot 连接（发送消息等）
self.llm          - LLM 管理器
self.scheduler    - 定时任务调度器（可能为 None，需判空）
self.tool_registry - Function Calling 工具注册表（可能为 None）
self.knowledge_store - 知识库（可能为 None）
"""

import logging
from datetime import datetime, timezone

from bot.plugin.base import PluginBase
from bot.plugin.matcher import (
    MatcherContext,
    on_command,
    on_keyword,
    on_message,
    on_notice,
    on_request,
    on_startswith,
)
from bot.plugin.permission import SUPERUSER
from bot.plugin.rule import command, is_private

logger = logging.getLogger("qingci-bot.plugin.template")


class TemplatePlugin(PluginBase):
    """插件开发模板 —— 演示所有可用功能

    复制后修改类名和 name 属性，其余按需删减。
    """

    # ========== 插件元信息（必填 name，其余可选）==========
    name = "template"
    version = "1.0.0"
    author = "YourName"
    description = "插件开发模板（演示所有功能）"

    # ========== 依赖声明（可选）==========
    # 声明的插件会先于本插件加载；依赖缺失或循环依赖时加载失败
    require: list[str] = []  # 如 ["chat", "admin"]

    # ========== 生命周期 ==========

    async def on_load(self):
        """插件加载时调用 —— 在此注册 Matcher、定时任务、工具等"""
        logger.info(f"[{self.name}] 插件加载中...")

        # ─── 1. 命令 Matcher ───────────────────────────────────
        # on_command 自动解析 /命令名 参数，ctx.command="命令名" ctx.args="参数"
        # 支持别名：("ping", "p") 两个名字都触发
        self.matchers.append(
            on_command(
                ("ping", "p"),
                description="回复 pong",
                priority=1,
            )(self._cmd_ping)
        )

        # ─── 2. 前缀 Matcher ───────────────────────────────────
        # 消息以指定前缀开头时触发，ctx.args 为前缀后文本
        self.matchers.append(
            on_startswith(
                ("天气", "weather"),
                description="查询天气",
                priority=10,
            )(self._cmd_weather)
        )

        # ─── 3. 关键词 Matcher ─────────────────────────────────
        # 消息含任一关键词时触发
        self.matchers.append(
            on_keyword(
                ("帮助", "help", "菜单"),
                description="显示帮助",
                priority=5,
            )(self._cmd_help)
        )

        # ─── 4. 消息 Matcher（带权限 + 规则组合）─────────────────
        # 仅管理员可用的私聊命令
        self.matchers.append(
            on_message(
                rule=command("admin") & is_private(),
                permission=SUPERUSER,
                priority=1,
                description="管理命令（仅管理员私聊）",
            )(self._cmd_admin)
        )

        # ─── 5. 通知事件 Matcher ───────────────────────────────
        # 监听群成员增加事件
        self.matchers.append(
            on_notice(
                priority=1,
            )(self._on_group_notice)
        )

        # ─── 6. 请求事件 Matcher ───────────────────────────────
        # 自动同意加好友请求
        self.matchers.append(
            on_request(
                priority=1,
            )(self._on_friend_request)
        )

        # ─── 7. 一次性 Matcher（temp=True）───────────────────────
        # 执行一次后自动移除，适合"下一条消息"场景
        self.matchers.append(
            on_message(
                rule=command("next"),
                temp=True,
                priority=1,
                description="等待下一条消息（一次性）",
            )(self._cmd_next_message)
        )

        # ─── 8. 定时任务（需 self.scheduler 可用）────────────────
        if self.scheduler is not None:
            # 每小时执行
            self.scheduler.add_job(
                self._hourly_task,
                trigger="interval",
                job_id="hourly_check",
                owner=self.name,
                hours=1,
            )
            # 每天 8:00 执行
            self.scheduler.add_job(
                self._daily_task,
                trigger="cron",
                job_id="daily_report",
                owner=self.name,
                hour=8,
                minute=0,
            )

        # ─── 9. 注册 Function Calling 工具（需 self.tool_registry 可用）─
        if self.tool_registry is not None:
            self.tool_registry.register(
                name="get_time",
                description="获取当前时间",
                parameters={
                    "type": "object",
                    "properties": {
                        "timezone_offset": {
                            "type": "number",
                            "description": "时区偏移（小时），默认 8（北京时间）",
                        }
                    },
                },
                handler=self._tool_get_time,
            )

        logger.info(f"[{self.name}] 插件加载完成 (matchers: {len(self.matchers)})")

    async def on_unload(self):
        """插件卸载时调用 —— 清理定时任务、关闭连接等"""
        logger.info(f"[{self.name}] 插件卸载中...")

        # 定时任务由 PluginManager 自动按 owner 清理，无需手动操作
        # 但如果有额外的异步资源（如 aiohttp session），应在此关闭

        logger.info(f"[{self.name}] 插件已卸载")

    # ========== 命令处理器 ==========

    async def _cmd_ping(self, ctx: MatcherContext) -> str:
        """简单命令：/ping -> pong"""
        return "pong!"

    async def _cmd_weather(self, ctx: MatcherContext) -> str:
        """前缀匹配：天气 北京 -> ctx.args = '北京'"""
        city = ctx.args.strip() or "未知城市"
        return f"查询 {city} 的天气...（示例）"

    async def _cmd_help(self, ctx: MatcherContext) -> str:
        """关键词匹配：消息含"帮助"即触发"""
        return "这是模板插件的帮助信息。"

    async def _cmd_admin(self, ctx: MatcherContext) -> str:
        """管理员命令（仅 SUPERUSER 在私聊中可用）"""
        # 通过 self.connection 调用 OneBot API
        # 通过 self.llm 调用大模型
        # 通过 self.db 访问数据库
        return "管理员命令已执行"

    async def _cmd_next_message(self, ctx: MatcherContext) -> str:
        """一次性匹配器：触发后自动移除"""
        return "请发送下一条消息...（此命令已失效）"

    # ========== 通知/请求处理器 ==========

    async def _on_group_notice(self, ctx: MatcherContext) -> str:
        """群通知事件处理"""
        event = ctx.raw_event or {}
        notice_type = event.get("notice_type", "")

        if notice_type == "group_increase":
            user_id = event.get("user_id", "未知")
            return f"欢迎新成员 {user_id} 加入群聊！"

        return None  # 不处理的事件返回 None

    async def _on_friend_request(self, ctx: MatcherContext) -> str:
        """加好友请求处理"""
        event = ctx.raw_event or {}
        user_id = event.get("user_id", "未知")
        comment = event.get("comment", "")

        logger.info(f"收到好友请求: user_id={user_id}, comment={comment}")
        return "已自动同意好友请求"  # 返回非 None 即同意

    # ========== 定时任务 ==========

    async def _hourly_task(self):
        """每小时执行的任务"""
        now = datetime.now(timezone.utc).astimezone()
        logger.info(f"[{self.name}] 每小时任务执行: {now.strftime('%H:%M')}")

    async def _daily_task(self):
        """每天 8:00 执行的任务"""
        # 可通过 self.connection 发送消息到指定群
        # 可通过 self.db 查询统计数据
        logger.info(f"[{self.name}] 每日任务执行")

    # ========== Function Calling 工具 ==========

    async def _tool_get_time(self, timezone_offset: int = 8) -> str:
        """LLM 可调用的工具：获取当前时间"""
        from datetime import timedelta

        tz = timezone(timedelta(hours=timezone_offset))
        now = datetime.now(tz)
        return now.strftime("%Y-%m-%d %H:%M:%S")

    # ========== 旧式消息处理（兼容）==========

    async def on_message(self, ctx):
        """旧式消息处理：Matcher 均未匹配时回退到此方法"""
        # 返回非 None 则作为回复发送
        return None


# ========== 模块级装饰器（可选）==========
# 也可在模块顶层用装饰器注册，PluginManager 加载时自动收集


@on_command("status", description="查看状态")
async def status_handler(ctx: MatcherContext) -> str:
    """模块级命令处理器 —— 通过 ctx.bot 访问依赖"""
    if ctx.bot is None:
        return "Bot 未就绪"
    status = ctx.bot.get_status()
    return (
        f"运行状态: {'运行中' if status['running'] else '已停止'}\n"
        f"已连接: {'是' if status['connected'] else '否'}\n"
        f"插件数: {len(status['plugins'])}"
    )
