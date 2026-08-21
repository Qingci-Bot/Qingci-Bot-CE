// 统一 API 请求层（单一入口）
//
// 职责：
// - 自动注入 X-API-Key 鉴权头（配置的 key 存 localStorage）
// - JSON 解析（204 → null；非 JSON 响应体 → 明确报错）
// - 401 统一处理：失效路由鉴权缓存 + 跳转登录页（可经 skipAuthRedirect 跳过，
//   供登录页等需要自行呈现 401 错误的场景使用）
//
// 用法：
//   import { request } from '../api/request';
//   const data = await request('/api/bot/status');
//   await request('/api/bot/start', { method: 'POST' });

import { invalidateAuthStatusCache } from '../router/index.js';

const API = '';

export function getApiKey() {
  return localStorage.getItem('qingci_api_key') || '';
}

export function setApiKey(key) {
  if (key) {
    localStorage.setItem('qingci_api_key', key);
  } else {
    localStorage.removeItem('qingci_api_key');
  }
}

export function authHeaders(extra = {}) {
  const key = getApiKey();
  const headers = { ...extra };
  if (key) {
    headers['X-API-Key'] = key;
  }
  return headers;
}

/**
 * 发起 API 请求
 * @param {string} url 接口路径（/api/...）
 * @param {Object} options fetch 选项；额外支持 skipAuthRedirect: true 跳过 401 跳转
 * @returns {Promise<Object|null>} 解析后的 JSON；204 返回 null
 */
export async function request(url, options = {}) {
  const { skipAuthRedirect = false, ...restOptions } = options;
  const headers = authHeaders(options.headers || {});
  // GET 强制禁用浏览器缓存：避免保存配置后再次拉取命中启发式缓存，导致页面回显旧值
  const method = String(restOptions.method || 'GET').toUpperCase();
  const fetchOptions = {
    ...restOptions,
    headers,
    ...(method === 'GET' ? { cache: 'no-store' } : {}),
  };
  const res = await fetch(`${API}${url}`, fetchOptions);
  if (res.status === 401) {
    // 优先取服务端 detail（如登录接口的"API Key 错误"），否则用通用提示
    let detail = '';
    try {
      const body = await res.json();
      detail = (body && body.detail) || '';
    } catch (parseErr) {
      // 非 JSON 响应体，使用通用提示
    }
    const message = detail || 'API Key 鉴权失败，请在设置中配置正确的 API Key';
    if (!skipAuthRedirect) {
      // 失效路由层的鉴权状态缓存，使跳转登录后能重新拉取最新状态
      invalidateAuthStatusCache();
      // 跳转登录页（hash 模式直改 location，避免与 router 循环依赖）
      if (window.location.hash !== '#/login') {
        window.location.hash = '#/login';
      }
    }
    throw new Error(message);
  }
  if (!res.ok) {
    const text = await res.text();
    let message = text || `HTTP ${res.status}`;
    try {
      const body = JSON.parse(text);
      if (body && body.detail) message = body.detail;
    } catch (parseErr) {
      // 非 JSON 响应体，使用原文
    }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  try {
    return await res.json();
  } catch (parseErr) {
    throw new Error(`HTTP ${res.status} 响应不是有效 JSON`);
  }
}
