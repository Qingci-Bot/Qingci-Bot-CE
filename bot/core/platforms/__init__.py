"""多平台适配器层

平台适配器将「平台协议」归一化为 Qingci-Bot 内部契约（OneBot-11 兼容
事件 dict + 统一发送接口），使插件/内置功能对平台无感知：

- 事件上报：适配器把平台事件归一化为 OneBot-11 兼容 dict
  （post_type / message_type / user_id / group_id / message 段等），
  并附带 platform 字段标记来源（默认 onebot）
- 发送：send_msg(message_type, target_id, text) 统一入口，由 Bot 按
  MessageContext.platform 路由到对应适配器
- 能力透传：call_api(action, params) 暴露平台能力（OneBot API 或
  Telegram Bot API），未实现的 action 抛 NotImplementedError

内置适配器：
- OneBotConnection（bot/core/connection.py）：OneBot 11 反向 WebSocket
- TelegramAdapter（telegram.py）：Telegram Bot API 长轮询
"""

from .base import PlatformAdapter
from .telegram import TelegramAdapter

__all__ = ["PlatformAdapter", "TelegramAdapter"]
