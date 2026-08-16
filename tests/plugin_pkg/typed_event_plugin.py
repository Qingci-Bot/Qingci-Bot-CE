"""类型化事件测试插件

演示 notice/request handler 通过参数注解注入类型化事件对象：
- GroupIncreaseNotice / GroupBanNotice 等通知子类
- GroupRequestEvent / FriendRequestEvent 请求子类
- 通用 NoticeEvent / RequestEvent 基类
"""

from bot.plugin.base import PluginBase
from bot.plugin.events import (
    FriendRequestEvent,
    GroupBanNotice,
    GroupIncreaseNotice,
    GroupRequestEvent,
    NoticeEvent,
)
from bot.plugin.matcher import MatcherContext, on_notice, on_request


class TypedEventPlugin(PluginBase):
    name = "typed_event"
    version = "1.0.0"
    description = "类型化事件测试插件"

    async def on_load(self):
        self.matchers.append(on_notice(priority=1)(self._on_group_increase))
        self.matchers.append(on_notice(priority=2)(self._on_group_ban))
        self.matchers.append(on_request(priority=1)(self._on_group_request))
        self.matchers.append(on_request(priority=2)(self._on_friend_request))

    async def on_unload(self):
        pass

    async def _on_group_increase(self, ctx: MatcherContext, event: GroupIncreaseNotice) -> str:
        """群成员增加：注入类型化子类"""
        return f"欢迎 {event.user_id}（由 {event.operator_id} 操作）入群 {event.group_id}"

    async def _on_group_ban(self, ctx: MatcherContext, event: GroupBanNotice) -> str:
        """群禁言：注入类型化子类"""
        return f"禁言 {event.user_id} {event.duration} 秒（{event.sub_type}）"

    async def _on_group_request(self, ctx: MatcherContext, event: GroupRequestEvent) -> bool:
        """加群请求：注入类型化子类，返回审批结果"""
        assert event.group_id > 0  # 类型化字段可用
        return event.sub_type == "add"  # add 同意，invite 拒绝

    async def _on_friend_request(self, ctx: MatcherContext, event: FriendRequestEvent) -> bool:
        """加好友请求：注入类型化子类"""
        if "拒绝" in event.comment:
            return False
        return True

    async def _legacy_notice(self, ctx: MatcherContext, event: NoticeEvent) -> str:
        """通用基类注入（兼容）"""
        return f"notice: {event.notice_type}"
