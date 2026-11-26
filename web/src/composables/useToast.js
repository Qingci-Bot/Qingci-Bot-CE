// Copyright (C) 2026 Zhou Zhe (aka luoqingci)
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms under GPLv3 Section 7: see NOTICE

import { ref } from 'vue';

// 模块级单例：所有组件共享同一个 toast 队列，由 App.vue 统一渲染顶部悬浮通知。
// 队列式而非单实例：连续提示（如批量操作逐条反馈）不再互相覆盖。
const toasts = ref([]);
const TOAST_TTL_MS = 4000;
const MAX_TOASTS = 5; // 超出后丢弃最旧的，防止异常循环请求把队列刷爆
let nextId = 1;

export function showToast(type, message) {
  const id = nextId++;
  toasts.value.push({ id, type, message });
  if (toasts.value.length > MAX_TOASTS) {
    toasts.value.shift();
  }
  setTimeout(() => dismissToast(id), TOAST_TTL_MS);
}

export function dismissToast(id) {
  const idx = toasts.value.findIndex((t) => t.id === id);
  if (idx !== -1) toasts.value.splice(idx, 1);
}

export function useToast() {
  return { toasts, showToast, dismissToast };
}
