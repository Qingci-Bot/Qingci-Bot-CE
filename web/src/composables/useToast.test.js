// 全局 Toast 状态测试
//
// 关注点：模块级单例的可见性、4 秒自动隐藏、重复调用时重置计时器
// （否则第二次 toast 会被第一次的计时器提前关掉）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { showToast, useToast } from './useToast';

describe('composables/useToast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    // 复位单例状态，避免用例间相互污染
    useToast().toast.value.show = false;
  });

  it('showToast 立即展示指定类型与文案', () => {
    showToast('error', '保存失败');

    const { toast } = useToast();
    expect(toast.value).toMatchObject({ show: true, type: 'error', message: '保存失败' });
  });

  it('4 秒后自动隐藏', () => {
    showToast('info', '已保存');
    const { toast } = useToast();

    vi.advanceTimersByTime(3999);
    expect(toast.value.show).toBe(true);

    vi.advanceTimersByTime(1);
    expect(toast.value.show).toBe(false);
  });

  it('连续调用会重置计时器，后一条不会被前一条的计时器提前关闭', () => {
    const { toast } = useToast();

    showToast('info', '第一条');
    vi.advanceTimersByTime(3000);

    showToast('success', '第二条');
    // 距第一条已过 3 秒，若计时器未重置，此处会被关掉
    vi.advanceTimersByTime(2000);
    expect(toast.value.show).toBe(true);
    expect(toast.value.message).toBe('第二条');

    // 距第二条满 4 秒后才隐藏
    vi.advanceTimersByTime(2000);
    expect(toast.value.show).toBe(false);
  });

  it('所有调用方共享同一份 toast 状态（模块级单例）', () => {
    const a = useToast();
    const b = useToast();

    showToast('success', '共享');

    expect(a.toast).toBe(b.toast);
    expect(b.toast.value.message).toBe('共享');
  });
});
