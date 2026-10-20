// 全局 Store 测试
//
// 关注点：派生状态（statusText/statusColor）映射、日志环形上限、
// 拉取失败时保留上次状态（避免轮询抖动误报"未启动"）。
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAppStore } from './app';

function mockJson(data, status = 200) {
  globalThis.fetch = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  }));
}

function mockFailure(status = 500, body = '服务器错误') {
  globalThis.fetch = vi.fn(async () => ({
    ok: false,
    status,
    json: async () => ({ detail: body }),
    text: async () => body,
  }));
}

describe('stores/app', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    window.location.hash = '';
  });

  describe('派生状态', () => {
    it('未启动时文案与颜色为灰色', () => {
      const store = useAppStore();
      expect(store.statusText).toBe('未启动');
      expect(store.statusColor).toBe('#6b7280');
    });

    it('已启动但未连接协议端时提示等待', () => {
      const store = useAppStore();
      store.botRunning = true;
      store.botConnected = false;

      expect(store.statusText).toBe('等待协议端连接');
      expect(store.statusColor).toBe('#f59e0b');
    });

    it('运行且已连接时为运行中（绿色）', () => {
      const store = useAppStore();
      store.botRunning = true;
      store.botConnected = true;

      expect(store.statusText).toBe('运行中');
      expect(store.statusColor).toBe('#10b981');
    });
  });

  describe('日志缓冲', () => {
    it('新日志插入队首', () => {
      const store = useAppStore();
      store.addLog({ id: 1 });
      store.addLog({ id: 2 });

      expect(store.logs.map((l) => l.id)).toEqual([2, 1]);
    });

    it('超过 200 条时丢弃最旧的一条（环形上限）', () => {
      const store = useAppStore();
      for (let i = 0; i < 250; i += 1) {
        store.addLog({ id: i });
      }

      expect(store.logs).toHaveLength(200);
      expect(store.logs[0].id).toBe(249);
      expect(store.logs[199].id).toBe(50);
    });
  });

  describe('状态拉取', () => {
    it('成功时写入运行状态、版本与平台列表', async () => {
      mockJson({
        running: true,
        connected: true,
        version: '1.17.3',
        platforms: [{ name: 'onebot' }],
        plugins: [{ name: 'chat' }],
      });
      const store = useAppStore();

      await expect(store.fetchStatus()).resolves.toBe(true);
      expect(store.botRunning).toBe(true);
      expect(store.botConnected).toBe(true);
      expect(store.appVersion).toBe('1.17.3');
      expect(store.platforms).toHaveLength(1);
      expect(store.plugins).toHaveLength(1);
      expect(store.error).toBe('');
    });

    it('失败时返回 false 并保留上次状态（防轮询抖动误报未启动）', async () => {
      const store = useAppStore();
      store.botRunning = true;
      store.botConnected = true;
      mockFailure(503, '服务暂不可用');

      await expect(store.fetchStatus()).resolves.toBe(false);
      expect(store.botRunning).toBe(true);
      expect(store.botConnected).toBe(true);
      expect(store.error).toBe('服务暂不可用');
    });
  });

  describe('实例', () => {
    it('currentInstance 只取运行中的实例', async () => {
      mockJson([
        { name: 'a', running: false },
        { name: 'b', running: true },
      ]);
      const store = useAppStore();

      await store.fetchInstances();

      expect(store.currentInstance).toMatchObject({ name: 'b' });
    });

    it('无运行中实例时为 null', async () => {
      mockJson([{ name: 'a', running: false }]);
      const store = useAppStore();

      await store.fetchInstances();

      expect(store.currentInstance).toBeNull();
    });
  });
});
