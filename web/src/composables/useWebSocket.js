// Copyright (C) 2026 Zhou Zhe (aka luoqingci)
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms under GPLv3 Section 7: see NOTICE

// WebSocket 统一封装：token 注入（api-key.<token> 子协议）+ 断线自动重连
//
// 重连策略：指数退避（3s 起步，每次翻倍，上限 30s），连接成功即重置；
// 服务端以 4001（鉴权失败）关闭时停止重连——无限重试只会反复被拒。
//
// 用法：
//   import { useWebSocket } from '../composables/useWebSocket';
//   const { connected, connect, disconnect, send } = useWebSocket('/api/ws/log', {
//     onMessage: (event) => { ... },
//   });
//   onMounted(connect);
//   onUnmounted(disconnect);

import { ref } from 'vue';
import { getApiKey } from '../api/request';

const RECONNECT_BASE_MS = 3000;
const RECONNECT_MAX_MS = 30000;
// 服务端约定的鉴权失败关闭码（api/server.py：_ws_auth_check 失败 → close(4001)）
const WS_CLOSE_AUTH_FAILED = 4001;

export function useWebSocket(path, { onMessage, onOpen, onClose } = {}) {
  const connected = ref(false);
  let socket = null;
  let reconnectTimer = null;
  let shouldReconnect = false;
  let reconnectDelay = RECONNECT_BASE_MS;

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  }

  function connect() {
    if (!shouldReconnect) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getApiKey() || '';
    const protocols = token ? [`api-key.${token}`] : [];
    socket = new WebSocket(`${proto}//${location.host}${path}`, protocols);
    socket.onopen = () => {
      connected.value = true;
      // 连接成功即重置退避，恢复正常响应速度
      reconnectDelay = RECONNECT_BASE_MS;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (onOpen) onOpen(socket);
    };
    socket.onclose = (event) => {
      connected.value = false;
      if (onClose) onClose();
      if (!shouldReconnect) return;
      if (event.code === WS_CLOSE_AUTH_FAILED) {
        // 鉴权被明确拒绝：重连只会重复失败，停止并交由 401 统一处理流程引导登录
        shouldReconnect = false;
        return;
      }
      scheduleReconnect();
    };
    socket.onmessage = (event) => {
      if (onMessage) onMessage(event, socket);
    };
  }

  function start() {
    shouldReconnect = true;
    connect();
  }

  function stop() {
    shouldReconnect = false;
    closeSocket();
  }

  // 仅关闭当前连接但保持重连（供"停止当前流式"等场景，随后自动重连）
  function close() {
    closeSocket();
  }

  function closeSocket() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket) {
      socket.close();
      socket = null;
    }
  }

  function send(data) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(data);
    }
  }

  return { connected, connect: start, disconnect: stop, close, send };
}
