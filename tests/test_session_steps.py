"""会话阶梯（多轮交互）功能测试

验证 SDK Session 阶梯 API 在主项目 Dispatcher 中的完整链路：
- pause 挂起后同会话下一条消息续接同一 handler
- session 自定义属性跨轮保留
- finish 结束阶梯后不再续接
- reject 拒绝输入继续等待
- 会话键隔离（不同用户互不干扰）
- 阶梯超时后自动失效

阶梯的提示文本通过 connection 主动发送（与真实 Bot 语义一致），
测试用 bot.sent_messages 断言。
"""

import logging
import time

import pytest

from bot.testing import TestBot, private_message


@pytest.fixture
def bot():
    return TestBot()


def last_sent(bot) -> str | None:
    """取最后一条主动发送的消息文本"""
    msgs = bot.sent_messages
    return msgs[-1][2] if msgs else None


async def test_pause_resume_finish(bot):
    """向导流程：pause 两次 + finish，session 属性跨轮保留"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")

    r1 = await bot.send(private_message("/wizard"))
    assert r1 is None  # 阶梯文本走主动发送通道
    assert last_sent(bot) == "请输入你的名字："

    # 无 / 前缀的普通文本也续接（跳过命令前缀规则）
    r2 = await bot.send(private_message("晴"))
    assert r2 is None
    assert last_sent(bot) == "你好 晴，请输入你的年龄："

    r3 = await bot.send(private_message("18"))
    assert r3 is None
    assert last_sent(bot) == "向导完成：晴，18岁"  # finish 文本走主动发送通道

    # 阶梯已结束：再次发送普通文本不应续接（无回复）
    r4 = await bot.send(private_message("随便说说"))
    assert r4 is None


async def test_reject_until_valid(bot):
    """reject：拒绝非数字输入，直到收到合法值才 finish"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")

    await bot.send(private_message("/survey"))
    assert last_sent(bot) == "请给本次服务打分（1-10）："

    await bot.send(private_message("abc"))
    assert last_sent(bot) == "输入无效，请输入数字："

    r3 = await bot.send(private_message("10"))
    assert r3 is None
    assert last_sent(bot) == "感谢评分：10 分"


async def test_reject_until_confirmed(bot):
    """校验型阶梯：必须回复 yes"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")

    await bot.send(private_message("/validated"))
    assert last_sent(bot) == "确认删除全部数据？回复 yes 继续："

    await bot.send(private_message("no"))
    assert last_sent(bot) == "请回复 yes 确认："

    r3 = await bot.send(private_message("yes"))
    assert r3 is None
    assert last_sent(bot) == "已确认，数据已删除。"


async def test_session_isolated_by_user(bot):
    """会话阶梯按用户隔离：另一用户发消息不续接当前阶梯"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")

    await bot.send(private_message("/wizard", user_id=10001))
    assert last_sent(bot) == "请输入你的名字："

    # 另一用户发消息：无阶梯，无回复
    r_other = await bot.send(private_message("hello", user_id=10002))
    assert r_other is None

    # 原用户继续续接
    await bot.send(private_message("晴", user_id=10001))
    assert last_sent(bot) == "你好 晴，请输入你的年龄："


async def test_step_timeout_invalidates(bot):
    """阶梯超时（默认 300s）后自动失效，不再续接"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")

    await bot.send(private_message("/wizard"))
    assert last_sent(bot) == "请输入你的名字："

    # 手动快进阶梯过期时间
    key = f"private:{10001}"
    async with bot.dispatcher._steps_lock:
        step = bot.dispatcher._pending_steps[key]
        step.expire_at = time.monotonic() - 1  # 已过期

    r2 = await bot.send(private_message("晴"))
    assert r2 is None  # 超时后不续接，也无其他匹配
    # 过期阶梯已被清除
    async with bot.dispatcher._steps_lock:
        assert key not in bot.dispatcher._pending_steps


async def test_finish_clears_step(bot):
    """finish 后阶梯清除：_pending_steps 无残留"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")

    await bot.send(private_message("/survey"))
    await bot.send(private_message("5"))

    async with bot.dispatcher._steps_lock:
        assert bot.dispatcher._pending_steps == {}


async def test_unload_clears_steps(bot):
    """插件卸载时清除其挂起阶梯"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")
    await bot.send(private_message("/wizard"))

    async with bot.dispatcher._steps_lock:
        assert len(bot.dispatcher._pending_steps) == 1

    await bot.plugin_manager.unload("session_flow")

    async with bot.dispatcher._steps_lock:
        assert bot.dispatcher._pending_steps == {}


async def test_step_handler_plain_exception_consumes_event(bot, caplog):
    """阶梯续接时 handler 抛普通异常：事件被消费、阶梯不悬挂、不中断分发"""
    await bot.load_plugin("plugin_pkg.session_flow_plugin")
    await bot.send(private_message("/wizard"))
    assert last_sent(bot) == "请输入你的名字："

    key = f"private:{10001}"
    async with bot.dispatcher._steps_lock:
        step = bot.dispatcher._pending_steps[key]

        async def _boom(*args, **kwargs):
            raise RuntimeError("step handler boom")

        step.matcher.handler = _boom

    # 续接消息：handler 抛普通异常（非 Pause/Finish/Reject）
    with caplog.at_level(logging.ERROR, logger="qingci-bot.dispatcher"):
        r2 = await bot.send(private_message("晴"))
    assert r2 is None  # 事件被消费，无回复
    assert "step handler boom" in caplog.text  # 异常已被记录
    # 阶梯已删除，不残留悬挂状态（下一条同会话消息走正常分发）
    async with bot.dispatcher._steps_lock:
        assert key not in bot.dispatcher._pending_steps
