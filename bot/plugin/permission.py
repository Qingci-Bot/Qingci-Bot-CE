"""权限系统 - 借鉴 NoneBot2 的 Permission 设计

支持权限组合（AND/OR/NOT），内置常用权限：
- EVERYONE: 所有人（默认）
- SUPERUSER: 超级管理员（唯一，配置中的 super_admin）
- ADMIN: 普通管理员（配置中的 admin_users，多个；超级管理员自动包含在内）
- PRIVATE: 私聊
- GROUP: 群聊
- USER(user_ids): 指定用户
- GROUP_MEMBER(group_ids): 指定群的成员（仅群聊消息）

权限检查基于 event + ctx，返回 bool。
"""

import logging
from collections.abc import Callable

from ..core.dispatcher import MessageContext

logger = logging.getLogger("qingci-bot.permission")


class Permission:
    """权限对象，支持 & | ~ 组合"""

    def __init__(self, checker: Callable | None = None):
        # checker: async (bot, event, ctx) -> bool
        self._checkers: list[Callable] = []
        if checker is not None:
            self._checkers.append(checker)

    async def check(self, bot, event: dict, ctx: MessageContext) -> bool:
        """检查权限：所有 checker 都通过才返回 True（AND 逻辑）"""
        for checker in self._checkers:
            try:
                result = checker(bot, event, ctx)
                if hasattr(result, "__await__"):
                    result = await result
                if not result:
                    return False
            except Exception:
                logger.warning(f"权限 checker 异常: {checker!r}", exc_info=True)
                return False
        return True

    def __and__(self, other: "Permission") -> "Permission":
        """AND 组合：两者都满足"""
        perm = Permission()
        perm._checkers = self._checkers + other._checkers
        return perm

    def __or__(self, other: "Permission") -> "Permission":
        """OR 组合：满足其一即可"""
        left = self
        right = other

        async def combined_check(bot, event, ctx) -> bool:
            try:
                if await left.check(bot, event, ctx):
                    return True
            except Exception:
                return False
            try:
                return await right.check(bot, event, ctx)
            except Exception:
                return False

        return Permission(combined_check)

    def __invert__(self) -> "Permission":
        """NOT 组合：取反"""
        perm = Permission()
        original_checkers = self._checkers[:]

        async def _not_checker(bot, event, ctx):
            for c in original_checkers:
                r = c(bot, event, ctx)
                if hasattr(r, "__await__"):
                    r = await r
                if not r:
                    return True  # 原本不通过，取反后通过
            return False  # 原本全通过，取反后不通过

        perm._checkers = [_not_checker]
        return perm


# ============ 内置权限 ============

EVERYONE = Permission(lambda bot, event, ctx: True)
"""所有人"""


async def _is_superuser(bot, event, ctx):
    cfg = bot.config.bot if bot and bot.config else None
    if cfg is None:
        return False
    return ctx.user_id == getattr(cfg, "super_admin", None)


SUPERUSER = Permission(_is_superuser)
"""超级管理员（唯一，配置中的 super_admin）"""


async def _is_admin(bot, event, ctx):
    cfg = bot.config.bot if bot and bot.config else None
    if cfg is None:
        return False
    uid = ctx.user_id
    # 超级管理员自动继承普通管理员权限
    if uid == getattr(cfg, "super_admin", None):
        return True
    return uid in (cfg.admin_users or [])


ADMIN = Permission(_is_admin)
"""普通管理员（配置中的 admin_users，多个；超级管理员自动包含在内）"""


def _is_private(bot, event, ctx):
    return ctx.message_type == "private"


def _is_group(bot, event, ctx):
    return ctx.message_type == "group"


PRIVATE = Permission(_is_private)
"""私聊消息"""

GROUP = Permission(_is_group)
"""群聊消息"""

MEMBER = Permission(lambda bot, event, ctx: True)
"""普通群员（与 EVERYONE 等价但独立实例）"""


def USER(user_ids: int | list[int]) -> Permission:
    """指定用户可用"""
    ids = [user_ids] if isinstance(user_ids, int) else list(user_ids)
    return Permission(lambda bot, event, ctx: ctx.user_id in ids)


def GROUP_MEMBER(group_ids: int | list[int]) -> Permission:
    """指定群的成员可用（仅群聊消息生效）

    参数为群号列表；私聊消息一律不匹配。
    """
    ids = [group_ids] if isinstance(group_ids, int) else list(group_ids)
    return Permission(lambda bot, event, ctx: ctx.message_type == "group" and ctx.group_id in ids)
