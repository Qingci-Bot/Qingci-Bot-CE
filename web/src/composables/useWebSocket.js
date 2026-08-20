// WebSocket 统一封装：token 注入（api-key.<token> 子协议）+ 断线自动重连（3s）
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

export function useWebSocket(path, { onMessage, onOpen, onClose } = {}) {
  const connected = ref(false);
  let socket = null;
  let reconnectTimer = null;
  let shouldReconnect = false;

  function connect() {
    if (!shouldReconnect) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getApiKey() || '';
    const protocols = token ? [`api-key.${token}`] : [];
    socket = new WebSocket(`${proto}//${location.host}${path}`, protocols);
    socket.onopen = () => {
      connected.value = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (onOpen) onOpen(socket);
    };
    socket.onclose = () => {
      connected.value = false;
      if (onClose) onClose();
      if (shouldReconnect) {
        reconnectTimer = setTimeout(connect, 3000);
      }
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
