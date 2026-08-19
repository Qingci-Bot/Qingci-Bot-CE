"""会话状态管理器测试（SessionState / SessionStateManager）

覆盖：
- expire(ttl<=0) 永不过期语义（与 set 一致，而非立即过期）
- 会话数超限时驱逐最久未访问会话，且返回的实例一定注册进 _states
"""

from bot.core.session_state import SessionState, SessionStateManager

# ============ expire 语义 ============


def test_expire_positive_ttl():
    s = SessionState()
    s.set("k", "v")
    assert s.expire("k", 100) is True
    assert s.ttl("k") is not None
    assert s.get("k") == "v"


def test_expire_zero_ttl_means_never_expire():
    """expire(ttl=0) 视为永不过期（与 set 语义一致），而非立即过期"""
    s = SessionState()
    s.set("k", "v")
    assert s.expire("k", 0) is True
    assert s.ttl("k") == -1.0  # 永不过期标记
    assert s.get("k") == "v"


def test_expire_negative_ttl_means_never_expire():
    s = SessionState()
    s.set("k", "v")
    assert s.expire("k", -5) is True
    assert s.ttl("k") == -1.0
    assert s.get("k") == "v"


def test_expire_missing_key_returns_false():
    s = SessionState()
    assert s.expire("missing", 100) is False


# ============ 会话数超限驱逐 ============


async def test_max_sessions_evicts_oldest_accessed():
    """超限时删除最久未访问的会话，新会话正常注册进 _states"""
    m = SessionStateManager()
    m._max_sessions = 2

    s1 = await m.get_session(user_id=1)
    s1.set("k", "v1")
    s2 = await m.get_session(user_id=2)
    s2.set("k", "v2")
    assert len(m._states) == 2

    # 访问 s1 使其成为「最近访问」，s2 成为最旧
    await m.get_session(user_id=1)
    m._states["private:2"]._last_access = 1.0  # 手动把 s2 的访问时间置旧
    m._states["private:1"]._last_access = 2.0

    s3 = await m.get_session(user_id=3)
    s3.set("k", "v3")

    # 最旧的 private:2 被驱逐，private:1 保留
    assert "private:2" not in m._states
    assert "private:1" in m._states
    # 返回的实例一定注册进 _states（修复前会返回未注册的临时实例）
    assert m._states["private:3"] is s3
    assert len(m._states) == 2


async def test_max_sessions_evicts_and_state_survives():
    """驱逐后旧会话状态被清除，新会话读写正常（不静默丢失）"""
    m = SessionStateManager()
    m._max_sessions = 1

    s1 = await m.get_session(user_id=1)
    s1.set("name", "晴")
    m._states["private:1"]._last_access = 1.0

    s2 = await m.get_session(user_id=2)
    assert m._states["private:2"] is s2
    s2.set("name", "雨")
    assert s2.get("name") == "雨"

    # private:1 已被驱逐（其数据一并清除）
    assert "private:1" not in m._states


async def test_max_sessions_prefers_cleanup_over_evict():
    """存在空会话时优先清理而非驱逐活跃会话"""
    m = SessionStateManager()
    m._max_sessions = 2

    s1 = await m.get_session(user_id=1)
    s1.set("k", "v1")  # 活跃会话
    await m.get_session(user_id=2)  # 空会话（未写入任何键）
    assert len(m._states) == 2

    s3 = await m.get_session(user_id=3)
    s3.set("k", "v3")

    # 空会话 private:2 被清理，活跃的 private:1 保留，新会话注册成功
    assert "private:2" not in m._states
    assert "private:1" in m._states
    assert m._states["private:3"] is s3
    assert len(m._states) == 2
