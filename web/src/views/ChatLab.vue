<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue';
import { useAppStore } from '../stores/app';

const store = useAppStore();

// 调试会话固定 user_id（与后端 /api/ws/chat 默认值一致，避免污染真实对话）
const DEBUG_USER_ID = 900000001;

const messages = ref([]); // { role: 'user'|'assistant'|'error', text, streaming }
const input = ref('');
const wsConnected = ref(false);
const streaming = ref(false);
const chatBox = ref(null);
let socket = null;
let reconnectTimer = null;
let shouldReconnect = true;

onMounted(() => {
  connect();
});
onUnmounted(() => {
  shouldReconnect = false;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (socket) socket.close();
});

function connect() {
  if (!shouldReconnect) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = store.getApiKey() || '';
  const protocols = token ? [`api-key.${token}`] : [];
  socket = new WebSocket(`${proto}//${location.host}/api/ws/chat`, protocols);
  socket.onopen = () => {
    wsConnected.value = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };
  socket.onclose = () => {
    wsConnected.value = false;
    streaming.value = false;
    // 正在流式时断开视为停止
    const last = messages.value[messages.value.length - 1];
    if (last) last.streaming = false;
    if (shouldReconnect) {
      reconnectTimer = setTimeout(connect, 3000);
    }
  };
  socket.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    if (data.type === 'delta') {
      const last = messages.value[messages.value.length - 1];
      if (last && last.role === 'assistant' && last.streaming) {
        last.text += data.text;
      } else {
        messages.value.push({ role: 'assistant', text: data.text, streaming: true });
      }
      scrollDown();
    } else if (data.type === 'done') {
      const last = messages.value[messages.value.length - 1];
      if (last) last.streaming = false;
      streaming.value = false;
    } else if (data.type === 'error') {
      const last = messages.value[messages.value.length - 1];
      if (last && last.streaming) {
        last.text += `\n\n[错误] ${data.text}`;
        last.streaming = false;
      } else {
        messages.value.push({ role: 'error', text: data.text, streaming: false });
      }
      streaming.value = false;
    }
  };
}

function send() {
  const text = input.value.trim();
  if (!text || streaming.value || !socket || socket.readyState !== WebSocket.OPEN) return;
  messages.value.push({ role: 'user', text, streaming: false });
  messages.value.push({ role: 'assistant', text: '', streaming: true });
  streaming.value = true;
  socket.send(JSON.stringify({ message: text, user_id: DEBUG_USER_ID }));
  input.value = '';
  scrollDown();
}

function stop() {
  streaming.value = false;
  const last = messages.value[messages.value.length - 1];
  if (last) last.streaming = false;
  if (socket) socket.close();
}

async function clearSession() {
  if (!window.confirm('清空当前调试会话的历史记录？')) return;
  messages.value = [];
  try {
    await store.apiFetch(`/api/log/sessions/one?key=private:${DEBUG_USER_ID}`, {
      method: 'DELETE',
    });
  } catch (e) {
    console.warn('清空调试会话失败:', e);
  }
}

function scrollDown() {
  nextTick(() => {
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight;
  });
}
</script>

<template>
  <div class="page-header">
    <h1>对话调试</h1>
    <p>在浏览器中直接与 Bot 对话，流式查看回复（借鉴 AstrBot ChatUI）</p>
  </div>

  <div class="page-body chat-page">
    <div class="card chat-card">
      <div class="card-header">
        <div class="card-title">
          调试会话
          <span class="tag tag-status" :class="wsConnected ? 'tag-perm' : 'tag-danger'">
            {{ wsConnected ? '已连接' : '连接断开' }}
          </span>
        </div>
        <div class="action-bar">
          <button
            class="btn btn-secondary btn-sm"
            :disabled="!messages.length"
            @click="clearSession"
          >
            清空会话
          </button>
        </div>
      </div>

      <div ref="chatBox" class="chat-box">
        <div v-if="messages.length === 0" class="empty-state" style="padding: 40px">
          <div class="icon">◉</div>
          <div>发送消息开始调试对话</div>
          <div class="hint-text" style="margin-top: 8px">
            调试会话独立于平台真实对话（private:900000001），流式输出与 Bot 实机行为一致。
          </div>
        </div>
        <div v-for="(m, i) in messages" :key="i" class="chat-msg" :class="m.role">
          <span class="chat-label">{{
            m.role === 'user' ? '我' : m.role === 'error' ? '错误' : 'Bot'
          }}</span>
          <div class="chat-bubble">
            {{ m.text }}
            <span v-if="m.streaming" class="cursor">▋</span>
          </div>
        </div>
      </div>

      <div class="chat-input-bar">
        <input
          v-model="input"
          type="text"
          placeholder="输入消息，Enter 发送（支持流式回复）"
          :disabled="!wsConnected"
          @keyup.enter="send"
        />
        <button
          class="btn btn-secondary btn-sm"
          :disabled="streaming || !wsConnected"
          @click="send"
        >
          发送
        </button>
        <button v-if="streaming" class="btn btn-danger btn-sm" @click="stop">停止</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tag-status {
  margin-left: 10px;
}
.chat-page {
  flex: 1;
  display: flex;
}
.chat-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.chat-box {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  background: rgba(15, 23, 42, 0.35);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  margin-bottom: 14px;
}

.chat-msg {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 85%;
}
.chat-msg.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}
.chat-msg.error {
  align-self: center;
}

.chat-label {
  font-size: 12px;
  color: var(--text-muted);
  flex: none;
  padding-top: 6px;
  min-width: 28px;
  text-align: center;
}

.chat-bubble {
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid var(--border-color);
  color: var(--text-primary);
}
.chat-msg.user .chat-bubble {
  background: rgba(56, 189, 248, 0.18);
  border-color: rgba(56, 189, 248, 0.35);
}
.chat-msg.error .chat-bubble {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.3);
  color: var(--danger);
  font-size: 13px;
}

.cursor {
  display: inline-block;
  animation: blink 1s step-end infinite;
  color: var(--accent);
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}

.chat-input-bar {
  display: flex;
  gap: 10px;
  align-items: center;
}
.chat-input-bar input {
  flex: 1;
}
</style>
