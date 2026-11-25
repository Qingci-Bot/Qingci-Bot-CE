# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""命令路由：命令样式的消息必须由命令体系处理，不能被聊天插件吞掉

## 背景（真实反馈）

QQ 适配器下（官方机器人接口，用户身份是 openid 而不是 QQ 号）：
- 发 `/status` 收到的是聊天插件的一段回复（LLM 把命令当普通文本答了）
- 发 `/help` 只列出 3 条命令

两件事是同一个根因链：非 OneBot 平台的身份是 `openid` 字符串，管理员往
`bot.admin_users` 里填 QQ 号匹配不上 → 需要管理员权限的命令被跳过 →
`/help` 按权限过滤后只剩 EVERYONE 的少数几条；而 `/status` 被跳过后落到
聊天 Matcher（priority=50），LLM 就"回答"了它。

修法分三层，本文件逐一锁住：
1. 聊天规则不接管 `/` 开头的文本（不能让 LLM 臆测命令结果）
2. 命令兜底 Matcher 明确回「未找到命令」或「没有权限」并**打出当前身份**
3. `/help` 说明还有几条命令因权限未列出
"""

from __future__ import annotations

from bot.plugin.builtin.chat import chat_trigger
from bot.plugin.builtin.help import HelpPlugin
from bot.plugin.matcher import MatcherContext

ADMIN_ID = 10001  # TestBot 默认的 super_admin / admin_users
NORMAL_ID = 20002  # 非管理员


def _ctx(text: str, user_id: int = NORMAL_ID) -> MatcherContext:
    return MatcherContext(
        raw_event={"platform": "qq", "user_id": str(user_id)},
        type="message",
        detail_type="private",
        message_type="private",
        user_id=user_id,
        plain_text=text,
    )


async def _help_plugin(bot) -> HelpPlugin:
    """加载内置插件并取 help 实例

    不用 isinstance 断言：PluginManager 会 reload 模块，类对象与测试里
    import 到的可能不是同一个（isinstance 会假失败）。
    """
    await bot.plugin_manager.load_builtin(bot)
    plugin = bot.plugin_manager.get("help")
    assert plugin is not None
    return plugin


def _ensure_trigger_mode(bot, mode: str = "always") -> None:
    """TestBot 的 config.bot 是 SimpleNamespace，补上聊天规则要读的字段"""
    cfg = bot.config.bot
    if not hasattr(cfg, "trigger_mode"):
        cfg.trigger_mode = mode


# ──────────────────────────────────────────────────────────────────
# 1. 聊天规则不接管命令
# ──────────────────────────────────────────────────────────────────


async def test_chat_rule_skips_slash_command(bot):
    """`/status` 不得触发聊天规则

    否则 LLM 会把命令当普通文本回答（"当前一切正常"之类），用户以为命令生效了。
    """
    _ensure_trigger_mode(bot)
    rule = chat_trigger()
    assert await rule.check(bot, {}, _ctx("/status")) is False


async def test_chat_rule_still_accepts_plain_text(bot):
    """普通聊天不受影响（回归保护）"""
    _ensure_trigger_mode(bot)
    rule = chat_trigger()
    assert await rule.check(bot, {}, _ctx("今天天气怎么样")) is True


# ──────────────────────────────────────────────────────────────────
# 2. 命令兜底提示
# ──────────────────────────────────────────────────────────────────


async def test_unknown_command_reports_not_found(bot):
    """未注册的命令 → 明确说"未找到"，并引导去 /help"""
    plugin = await _help_plugin(bot)
    out = await plugin._handle_unhandled_command(_ctx("/nonexistent-cmd"))
    assert out is not None
    assert "未找到命令 /nonexistent-cmd" in out
    assert "/help" in out


async def test_denied_command_prints_identity_for_admin_config(bot):
    """有该命令但没权限 → 说明没有权限，并把当前身份打出来

    身份是 QQ 官方机器人场景下的 openid 字符串：管理员只有看到它，才知道
    该往 bot.admin_users 里填什么。只说「没有权限」等于没说。
    """
    plugin = await _help_plugin(bot)
    out = await plugin._handle_unhandled_command(_ctx("/status", user_id=NORMAL_ID))
    assert out is not None
    assert "没有权限执行 /status" in out
    assert str(NORMAL_ID) in out
    assert "admin_users" in out


async def test_authorized_command_is_not_mislabeled(bot):
    """有权限却落到兜底（命令自己选择不回复）时，不能误报"没有权限" """
    plugin = await _help_plugin(bot)
    # 管理员身份：/status 的权限检查会通过
    out = await plugin._handle_unhandled_command(_ctx("/status", user_id=ADMIN_ID))
    assert out is None


async def test_lone_slash_is_ignored(bot):
    """单个 "/" 不当作命令，不触发兜底提示"""
    plugin = await _help_plugin(bot)
    assert await plugin._handle_unhandled_command(_ctx("/")) is None


# ──────────────────────────────────────────────────────────────────
# 3. /help 说明被权限过滤掉的命令
# ──────────────────────────────────────────────────────────────────


async def test_help_reports_hidden_command_count(bot):
    """/help 必须说明还有几条命令因权限未列出，否则用户以为命令就这么多"""
    plugin = await _help_plugin(bot)
    # TestBot 的权限 checker 会因 config 缺字段异常（安全默认无权限），
    # 因此在 help 内部确实存在被过滤的命令，计数应大于 0
    result = await plugin._cmd_help(_ctx(""))

    # TestBot 无 html_renderer → 走纯文本分支，提示应拼在正文末尾
    if isinstance(result, str):
        assert "需要更高权限" in result
        assert str(NORMAL_ID) in result
        return
    # 有渲染能力时以追加文本段返回
    assert isinstance(result, list)
    texts = [seg["data"]["text"] for seg in result if seg.get("type") == "text"]
    assert texts, "应在图片之后附带隐藏命令提示"
    assert "需要更高权限" in texts[-1]
    assert str(NORMAL_ID) in texts[-1]


async def test_hidden_hint_absent_when_nothing_hidden(bot):
    """没有命令被权限过滤时，不应出现"另有 N 条"的噪音

    直接设置计数而不依赖真实插件的权限结果：TestBot 的 config.bot 是精简的
    SimpleNamespace，部分权限 checker 会因为缺字段抛异常（安全默认为无权限），
    那属于测试替身的局限，不该让本断言变脆。
    """
    plugin = await _help_plugin(bot)
    plugin._hidden_by_permission = 0
    assert plugin._hidden_hint(_ctx("", user_id=ADMIN_ID)) is None

    plugin._hidden_by_permission = 3
    hint = plugin._hidden_hint(_ctx("", user_id=NORMAL_ID))
    assert hint is not None and "另有 3 条" in hint["data"]["text"]
