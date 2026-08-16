"""单实例保护测试

验证命名互斥量的首个/重复实例判定逻辑（跨实例互斥行为仅 Windows 生效）。
"""

import sys
import uuid

import pytest

from desktop.single_instance import SingleInstance, bring_existing_to_front


@pytest.mark.skipif(sys.platform != "win32", reason="命名互斥量仅 Windows 有效")
class TestSingleInstance:
    def test_first_instance_acquires(self):
        inst = SingleInstance(name=f"qe-test-{uuid.uuid4()}")
        assert inst.acquire() is True
        inst.release()

    def test_second_instance_rejected(self):
        name = f"qe-test-{uuid.uuid4()}"
        first = SingleInstance(name=name)
        second = SingleInstance(name=name)
        assert first.acquire() is True
        try:
            assert second.acquire() is False
        finally:
            first.release()
            second.release()

    def test_release_allows_reacquire(self):
        name = f"qe-test-{uuid.uuid4()}"
        inst = SingleInstance(name=name)
        assert inst.acquire() is True
        inst.release()
        again = SingleInstance(name=name)
        try:
            assert again.acquire() is True
        finally:
            again.release()


def test_bring_existing_to_front_no_crash():
    """窗口不存在时调用不应抛异常（函数内已捕获）"""
    bring_existing_to_front("__not_a_real_window_title__")
