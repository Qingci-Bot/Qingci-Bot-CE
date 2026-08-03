"""权限系统 - 借鉴 NoneBot2 的 Permission 设计

支持权限组合（AND/OR/NOT），内置常用权限：
- EVERYONE: 所有人（默认）
- SUPERUSER / ADMIN: 配置中的管理员
- PRIVATE: 私聊
- GROUP: 群聊
- USER(user_ids): 指定用户
- GROUP_MEMBER(group_ids): 指定群成员

权限检查基于 event + ctx，返回 bool。
"""

from typing import Callable, Union

from ..core.dispatcher import MessageContext


class Permission:
    """权限对象，支持 & | ~ 组合"""

    def __init__(self, checker: Callable = None):
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
                return False
        return True

    def __and__(self, other: "Permission") -> "Permission":
        """AND 组合：两者都满足"""
        perm = Permission()
        perm._checkers = self._checkers + other._checkers
        return perm

    def __or__(self, other: "Permission") -> "Permission":
        """OR 组合：满足其一即可"""
        perm = Permission()
        left_checkers = self._checkers[:]
        right_checkers = other._checkers[:]

        async def _combined_or(bot, event, ctx):
            # 左侧全部通过即返回 True
            left_ok = True
            for c in left_checkers:
                r = c(bot, event, ctx)
                if hasattr(r, "__await__"):
                    r = await r
                if not r:
                    left_ok = False
                    break
            if left_ok:
                return True
            # 右侧全部通过即返回 True
            for c in right_checkers:
                r = c(bot, event, ctx)
                if hasattr(r, "__await__"):
                    r = await r
                if not r:
                    return False
            return True

        perm._checkers = [_combined_or]
        return perm

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
    admin_users = bot.config.bot.admin_users if bot and bot.config else []
    return ctx.user_id in admin_users


SUPERUSER = Permission(_is_superuser)
"""超级管理员（配置中的 admin_users）"""

ADMIN = SUPERUSER
"""管理员（SUPERUSER 别名）"""


def _is_private(bot, event, ctx):
    return ctx.message_type == "private"


def _is_group(bot, event, ctx):
    return ctx.message_type == "group"


PRIVATE = Permission(_is_private)
"""私聊消息"""

GROUP = Permission(_is_group)
"""群聊消息"""

MEMBER = EVERYONE
"""普通群员（默认所有人）"""


def USER(user_ids: Union[int, list[int]]) -> Permission:
    """指定用户可用"""
    ids = [user_ids] if isinstance(user_ids, int) else list(user_ids)
    return Permission(lambda bot, event, ctx: ctx.user_id in ids)


def GROUP_MEMBER(group_ids: Union[int, list[int]]) -> Permission:
    """指定群成员可用"""
    ids = [group_ids] if isinstance(group_ids, int) else list(group_ids)
    return Permission(lambda bot, event, ctx: ctx.group_id in ids)
