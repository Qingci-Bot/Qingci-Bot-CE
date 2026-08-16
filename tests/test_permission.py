"""Permission 权限系统测试：内置权限与组合逻辑"""

from bot.plugin.permission import (
    ADMIN,
    EVERYONE,
    GROUP,
    GROUP_MEMBER,
    MEMBER,
    PRIVATE,
    SUPERUSER,
    USER,
)


def make_ctx(user_id=0, group_id=0, message_type="private"):
    class Ctx:
        pass

    c = Ctx()
    c.user_id = user_id
    c.group_id = group_id
    c.message_type = message_type
    return c


async def check(perm, ctx, bot):
    return await perm.check(bot, {}, ctx)


class FakeBot:
    def __init__(self, admin_users=(), super_admin=None):
        class Cfg:
            def __init__(self, admins, sa):
                class Bot:
                    admin_users = admins
                    super_admin = sa

                self.bot = Bot()

        self.config = Cfg(list(admin_users), super_admin)


class TestBuiltinPermissions:
    async def test_everyone(self):
        bot = FakeBot()
        assert await check(EVERYONE, make_ctx(), bot)

    async def test_member(self):
        bot = FakeBot()
        assert await check(MEMBER, make_ctx(), bot)

    async def test_superuser(self):
        bot = FakeBot(super_admin=10001)
        assert await check(SUPERUSER, make_ctx(user_id=10001), bot)
        assert not await check(SUPERUSER, make_ctx(user_id=99999), bot)

    async def test_superuser_excludes_regular_admin(self):
        """普通管理员不是超级管理员"""
        bot = FakeBot(admin_users=[10001], super_admin=None)
        assert await check(ADMIN, make_ctx(user_id=10001), bot)
        assert not await check(SUPERUSER, make_ctx(user_id=10001), bot)

    async def test_admin_regular(self):
        bot = FakeBot(admin_users=[10001])
        assert await check(ADMIN, make_ctx(user_id=10001), bot)
        assert not await check(ADMIN, make_ctx(user_id=99999), bot)

    async def test_admin_super_inherits(self):
        """超级管理员自动继承普通管理员权限"""
        bot = FakeBot(super_admin=10001)
        assert await check(ADMIN, make_ctx(user_id=10001), bot)

    async def test_private(self):
        bot = FakeBot()
        assert await check(PRIVATE, make_ctx(message_type="private"), bot)
        assert not await check(PRIVATE, make_ctx(message_type="group"), bot)

    async def test_group(self):
        bot = FakeBot()
        assert await check(GROUP, make_ctx(message_type="group"), bot)
        assert not await check(GROUP, make_ctx(message_type="private"), bot)

    async def test_user(self):
        bot = FakeBot()
        assert await check(USER([1, 2, 3]), make_ctx(user_id=2), bot)
        assert not await check(USER([1, 2, 3]), make_ctx(user_id=4), bot)
        # 单个 int 入参
        assert await check(USER(5), make_ctx(user_id=5), bot)

    async def test_group_member(self):
        bot = FakeBot()
        assert await check(GROUP_MEMBER([10, 20]), make_ctx(group_id=10, message_type="group"), bot)
        assert not await check(
            GROUP_MEMBER([10, 20]), make_ctx(group_id=30, message_type="group"), bot
        )
        # 私聊消息不匹配群成员权限（即使 group_id 命中）
        assert not await check(
            GROUP_MEMBER([10, 20]), make_ctx(group_id=10, message_type="private"), bot
        )


class TestPermissionComposition:
    async def test_and(self):
        bot = FakeBot(super_admin=10001)
        r = SUPERUSER & PRIVATE
        assert await check(r, make_ctx(user_id=10001, message_type="private"), bot)
        assert not await check(r, make_ctx(user_id=10001, message_type="group"), bot)
        assert not await check(r, make_ctx(user_id=1, message_type="private"), bot)

    async def test_or(self):
        bot = FakeBot(super_admin=10001)
        r = SUPERUSER | PRIVATE
        assert await check(r, make_ctx(user_id=10001, message_type="group"), bot)
        assert await check(r, make_ctx(user_id=1, message_type="private"), bot)
        assert not await check(r, make_ctx(user_id=1, message_type="group"), bot)

    async def test_invert(self):
        bot = FakeBot(super_admin=10001)
        r = ~SUPERUSER
        assert await check(r, make_ctx(user_id=1), bot)
        assert not await check(r, make_ctx(user_id=10001), bot)

    async def test_chained(self):
        bot = FakeBot(super_admin=10001)
        r = (SUPERUSER & GROUP) | PRIVATE
        # 管理员群聊 ✓ / 任意私聊 ✓ / 非管理员群聊 ✗
        assert await check(r, make_ctx(user_id=10001, group_id=1, message_type="group"), bot)
        assert await check(r, make_ctx(user_id=1, message_type="private"), bot)
        assert not await check(r, make_ctx(user_id=1, group_id=1, message_type="group"), bot)
