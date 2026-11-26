// Copyright (C) 2026 Zhou Zhe (aka luoqingci)
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms under GPLv3 Section 7: see NOTICE

// 全局 Toast 队列测试
//
// 关注点：模块级单例、每条 4 秒自动消失、多条并存互不覆盖
// （单实例实现下连续提示会互相覆盖，改为队列后锁定此行为）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { showToast, useToast } from './useToast';

describe('composables/useToast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    // 复位单例状态，避免用例间相互污染
    useToast().toasts.value = [];
  });

  it('showToast 立即入队指定类型与文案', () => {
    showToast('error', '保存失败');

    const { toasts } = useToast();
    expect(toasts.value).toHaveLength(1);
    expect(toasts.value[0]).toMatchObject({ type: 'error', message: '保存失败' });
  });

  it('每条 toast 4 秒后自动消失', () => {
    showToast('info', '已保存');
    const { toasts } = useToast();

    vi.advanceTimersByTime(3999);
    expect(toasts.value).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(toasts.value).toHaveLength(0);
  });

  it('连续调用多条并存，各自独立计时', () => {
    const { toasts } = useToast();

    showToast('info', '第一条');
    vi.advanceTimersByTime(3000);

    showToast('success', '第二条');
    // 第一条已到 3 秒仍在展示；两条共存互不覆盖
    expect(toasts.value).toHaveLength(2);
    expect(toasts.value.map((t) => t.message)).toEqual(['第一条', '第二条']);

    // 再过 1 秒第一条到期消失，第二条保留
    vi.advanceTimersByTime(1000);
    expect(toasts.value.map((t) => t.message)).toEqual(['第二条']);

    vi.advanceTimersByTime(3000);
    expect(toasts.value).toHaveLength(0);
  });

  it('所有调用方共享同一份队列（模块级单例）', () => {
    const a = useToast();
    const b = useToast();

    showToast('success', '共享');

    expect(a.toasts).toBe(b.toasts);
    expect(b.toasts.value[0].message).toBe('共享');
  });

  it('队列超上限时丢弃最旧的（防异常循环请求刷爆）', () => {
    const { toasts } = useToast();
    for (let i = 0; i < 8; i++) showToast('info', `第${i}条`);

    expect(toasts.value.length).toBeLessThanOrEqual(5);
    expect(toasts.value[toasts.value.length - 1].message).toBe('第7条');
  });
});
