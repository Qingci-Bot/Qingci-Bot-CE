// API 请求层测试
//
// 覆盖鉴权头注入、GET 缓存策略、204/非 JSON 响应、401 跳转与跳过跳转、
// 错误消息优先级（服务端 detail 优先于原文）。
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { invalidateAuthStatusCache } from '../router/index.js';
import { authHeaders, getApiKey, request, setApiKey } from './request';

vi.mock('../router/index.js', () => ({
  invalidateAuthStatusCache: vi.fn(),
}));

/** 构造 fetch 响应替身 */
function mockResponse({ status = 200, json, text } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: json ? async () => json() : async () => JSON.parse('null'),
    text: text ? async () => text() : async () => '',
  };
}

describe('api/request', () => {
  beforeEach(() => {
    localStorage.clear();
    window.location.hash = '';
    globalThis.fetch = vi.fn();
  });

  describe('API Key 管理', () => {
    it('setApiKey 写入 localStorage，getApiKey 读回', () => {
      setApiKey('secret-key');
      expect(getApiKey()).toBe('secret-key');
      expect(localStorage.getItem('qingci_api_key')).toBe('secret-key');
    });

    it('setApiKey 传空值会清除已有 key', () => {
      setApiKey('secret-key');
      setApiKey('');
      expect(getApiKey()).toBe('');
      expect(localStorage.getItem('qingci_api_key')).toBeNull();
    });

    it('authHeaders 在配置了 key 时注入 X-API-Key', () => {
      setApiKey('secret-key');
      expect(authHeaders()).toEqual({ 'X-API-Key': 'secret-key' });
    });

    it('authHeaders 未配置 key 时不注入，且保留调用方原有头', () => {
      expect(authHeaders({ 'Content-Type': 'application/json' })).toEqual({
        'Content-Type': 'application/json',
      });
    });
  });

  describe('请求构造', () => {
    it('GET 强制 cache: no-store（避免配置回显旧值）', async () => {
      globalThis.fetch = vi.fn(async () => mockResponse({ json: () => ({ ok: 1 }) }));

      await request('/api/config');

      const [, options] = globalThis.fetch.mock.calls[0];
      expect(options.cache).toBe('no-store');
    });

    it('POST 不带 cache 选项', async () => {
      globalThis.fetch = vi.fn(async () => mockResponse({ json: () => ({}) }));

      await request('/api/bot/start', { method: 'POST' });

      const [, options] = globalThis.fetch.mock.calls[0];
      expect(options.cache).toBeUndefined();
    });

    it('204 返回 null', async () => {
      globalThis.fetch = vi.fn(async () => mockResponse({ status: 204 }));

      await expect(request('/api/x')).resolves.toBeNull();
    });
  });

  describe('错误处理', () => {
    it('401 优先使用服务端 detail，并跳转登录页 + 失效鉴权缓存', async () => {
      globalThis.fetch = vi.fn(async () =>
        mockResponse({ status: 401, json: () => ({ detail: 'API Key 错误' }) }),
      );

      await expect(request('/api/x')).rejects.toThrow('API Key 错误');
      expect(window.location.hash).toBe('#/login');
      expect(invalidateAuthStatusCache).toHaveBeenCalled();
    });

    it('401 响应体非 JSON 时回退通用提示', async () => {
      globalThis.fetch = vi.fn(async () =>
        mockResponse({
          status: 401,
          json: () => {
            throw new SyntaxError('not json');
          },
        }),
      );

      await expect(request('/api/x')).rejects.toThrow(/API Key 鉴权失败/);
    });

    it('skipAuthRedirect 时仍抛错但不跳转、不失效缓存', async () => {
      globalThis.fetch = vi.fn(async () =>
        mockResponse({ status: 401, json: () => ({ detail: '凭据无效' }) }),
      );

      await expect(request('/api/login', { skipAuthRedirect: true })).rejects.toThrow('凭据无效');
      expect(window.location.hash).toBe('');
      expect(invalidateAuthStatusCache).not.toHaveBeenCalled();
    });

    it('非 2xx 且响应体为 JSON 时取 detail 作为消息', async () => {
      globalThis.fetch = vi.fn(async () =>
        mockResponse({ status: 400, text: () => '{"detail":"参数非法"}' }),
      );

      await expect(request('/api/x')).rejects.toThrow('参数非法');
    });

    it('非 2xx 且响应体为纯文本时使用原文', async () => {
      globalThis.fetch = vi.fn(async () =>
        mockResponse({ status: 502, text: () => 'Bad Gateway' }),
      );

      await expect(request('/api/x')).rejects.toThrow('Bad Gateway');
    });

    it('2xx 但响应体不是合法 JSON 时给出明确错误', async () => {
      globalThis.fetch = vi.fn(async () =>
        mockResponse({
          status: 200,
          json: () => {
            throw new SyntaxError('unexpected token');
          },
        }),
      );

      await expect(request('/api/x')).rejects.toThrow(/不是有效 JSON/);
    });
  });
});
