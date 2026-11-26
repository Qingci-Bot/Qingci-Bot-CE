# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""`/status` 必须跟着适配器走，而不是写死 OneBot

## 背景（真实反馈）

QQ 官方机器人实例（`onebot.enabled=false`，只有 `qq` 适配器）里发 `/status`，
输出是「OneBot 连接: 离线」——适配器明明在线，报告却说离线。原因是这条命令
在只支持 OneBot 的年代写死了 `self.connection`（OneBot 主连接）。

现在改为遍历 `bot.platforms`（只含已启用的适配器），与 `bot.get_status()`
同一口径，并在离线时带上原因（凭据缺失 / 鉴权失败），让「离线」与「没配」可区分。
"""

from __future__ import annotations

from bot.plugin.matcher import MatcherContext

ADMIN_ID = 10001  # TestBot 默认的 super_admin


class _FakeAdapter:
    """最小适配器替身：只提供 _cmd_status 读取的字段"""

    def __init__(self, name: str, display_name: str, connected: bool, reason: str = ""):
        self.name = name
        self.display_name = display_name
        self.enabled = True
        self._start_error = reason
        self._connected = connected

    @property
    def is_connected(self) -> bool:
        return self._connected


def _ctx() -> MatcherContext:
    return MatcherContext(
        raw_event={"platform": "qq", "user_id": str(ADMIN_ID)},
        type="message",
        detail_type="private",
        message_type="private",
        user_id=ADMIN_ID,
        plain_text="/status",
    )


async def _admin_plugin(bot):
    await bot.plugin_manager.load_builtin(bot)
    plugin = bot.plugin_manager.get("admin")
    assert plugin is not None
    # 避免真实调用 LLM 探测可用性（会走网络）
    plugin._llm_check_cache = (True, 9e18)
    return plugin


async def test_status_lists_qq_adapter_instead_of_onebot(bot):
    """只有 QQ 适配器时，输出必须出现 QQ，且不得出现写死的「OneBot 连接」"""
    plugin = await _admin_plugin(bot)
    bot.platforms = {
        "qq": _FakeAdapter("qq", "QQ 官方机器人", connected=True),
    }
    text = await plugin._cmd_status(_ctx())

    assert "QQ 官方机器人" in text
    assert "在线" in text
    # 回归点：早期版本这里恒为「OneBot 连接: 离线」
    assert "OneBot 连接" not in text


async def test_status_reports_reason_when_offline(bot):
    """离线时必须带上原因，否则用户无法区分「没连上」与「没配凭据」"""
    plugin = await _admin_plugin(bot)
    bot.platforms = {
        "qq": _FakeAdapter("qq", "QQ 官方机器人", connected=False, reason="未配置 AppID"),
    }
    text = await plugin._cmd_status(_ctx())

    assert "离线" in text
    assert "未配置 AppID" in text


async def test_status_skips_disabled_adapters(bot):
    """停用的适配器不应出现在状态里（与 bot.get_status() 口径一致）"""
    plugin = await _admin_plugin(bot)
    disabled = _FakeAdapter("onebot", "OneBot v11", connected=False)
    disabled.enabled = False
    bot.platforms = {
        "onebot": disabled,
        "telegram": _FakeAdapter("telegram", "Telegram", connected=True),
    }
    text = await plugin._cmd_status(_ctx())

    assert "Telegram" in text
    assert "OneBot v11" not in text


async def test_status_lists_multiple_platforms(bot):
    """多平台实例应逐条列出，一行一个"""
    plugin = await _admin_plugin(bot)
    bot.platforms = {
        "onebot": _FakeAdapter("onebot", "OneBot v11", connected=True),
        "qq": _FakeAdapter("qq", "QQ 官方机器人", connected=False, reason="鉴权失败"),
    }
    text = await plugin._cmd_status(_ctx())

    lines = text.split("\n")
    platform_lines = [ln for ln in lines if ln.strip().startswith("·")]
    assert len(platform_lines) == 2
    assert any("OneBot v11" in ln and "在线" in ln for ln in platform_lines)
    assert any("QQ 官方机器人" in ln and "鉴权失败" in ln for ln in platform_lines)


async def test_status_without_any_platform_explains_what_to_do(bot):
    """没有任何启用平台时给出可操作提示，而不是空列表"""
    plugin = await _admin_plugin(bot)
    bot.platforms = {}
    text = await plugin._cmd_status(_ctx())

    assert "未启用" in text


# ──────────────────────────────────────────────────────────────────
# /group 的导入路径（顺手修掉的真 bug）
# ──────────────────────────────────────────────────────────────────


class _FakeGroupDb:
    """只实现 _cmd_group 用到的两个方法"""

    def __init__(self, existing: dict | None = None):
        self.existing = existing or {}
        self.saved: list[tuple] = []

    async def get_group_config(self, group_id: int):
        return self.existing.get(group_id)

    async def upsert_group_config(self, group_id: int, enabled: bool, trigger_mode):
        self.saved.append((group_id, enabled, trigger_mode))


async def test_group_command_does_not_crash_on_chat_import(bot):
    """/group 曾在写库之后 ImportError

    `from .chat import invalidate_group_config_cache` 解析到
    bot.plugin.builtin.admin.chat——不存在的模块。因为写在函数内，模块导入期
    不报错，只有真正执行 /group 才炸；而那时群配置**已经写进库**，
    用户看到的是报错但配置已改，最糟的一种失败。
    """
    from bot.plugin.matcher import MatcherContext

    plugin = await _admin_plugin(bot)
    plugin.db = _FakeGroupDb({100: {"trigger_mode": "always"}})

    ctx = MatcherContext(
        raw_event={"platform": "qq", "group_id": "100", "user_id": str(ADMIN_ID)},
        type="message",
        detail_type="group",
        message_type="group",
        user_id=ADMIN_ID,
        group_id=100,
        plain_text="/group off",
        args="off",  # 命令解析器只填 args，plain_text 不含命令参数
    )
    text = await plugin._cmd_group(ctx)

    assert "已关闭" in text
    # 保留了群原有的 trigger_mode，没有被重置
    assert plugin.db.saved == [(100, False, "always")]


# ──────────────────────────────────────────────────────────────────
# /whoami：身份识别码独立成命令
# ──────────────────────────────────────────────────────────────────


async def test_whoami_marks_super_admin(bot):
    plugin = await _admin_plugin(bot)
    bot.config.bot.super_admin = str(ADMIN_ID)
    text = await plugin._cmd_whoami(_ctx())
    assert "身份信息" in text
    assert str(ADMIN_ID) in text
    assert "超级管理员" in text
    # 已是管理员就不该再教他「去配置 admin_users」
    assert "bot.admin_users" not in text


async def test_whoami_marks_normal_user_and_tells_what_to_do(bot):
    """普通用户必须看到「把这串 ID 填到哪」——QQ 平台的身份是 openid 字符串"""
    plugin = await _admin_plugin(bot)
    bot.config.bot.super_admin = "999999"
    bot.config.bot.admin_users = ["888888"]
    text = await plugin._cmd_whoami(_ctx())
    assert "普通用户" in text
    assert "bot.admin_users" in text


async def test_whoami_includes_group_id_only_in_group(bot):
    from bot.plugin.matcher import MatcherContext

    plugin = await _admin_plugin(bot)
    private = await plugin._cmd_whoami(_ctx())
    assert "群 ID" not in private

    group_ctx = MatcherContext(
        raw_event={"platform": "qq", "group_id": "12345", "user_id": str(ADMIN_ID)},
        type="message",
        detail_type="group",
        message_type="group",
        user_id=ADMIN_ID,
        group_id=12345,
        plain_text="/whoami",
        args="",
    )
    assert "群 ID：12345" in await plugin._cmd_whoami(group_ctx)


async def test_whoami_registered_for_everyone(bot):
    """/whoami 必须所有人可用，否则普通用户查不到自己的 ID"""
    await bot.plugin_manager.load_builtin(bot)
    plugin = bot.plugin_manager.get("admin")
    matcher = next(m for m in plugin.matchers if (m.meta or {}).get("command") == "whoami")
    assert matcher.permission.label == "EVERYONE"
