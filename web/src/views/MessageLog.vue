<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { useAppStore } from '../stores/app';

const store = useAppStore();
const keyword = ref('');
const wsConnected = ref(false);
let socket = null;
let shouldReconnect = true;
let reconnectTimer = null;

// 会话记录
const activeTab = ref('messages');
const sessions = ref([]);
const currentSession = ref('');
const sessionMessages = ref([]);

onMounted(() => {
  store.fetchLogs('', 50).catch((e) => console.warn('初始消息日志加载失败:', e));
  connectWebSocket();
});

onUnmounted(() => {
  shouldReconnect = false;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (socket) socket.close();
});

function switchTab(tab) {
  activeTab.value = tab;
  if (tab === 'sessions' && sessions.value.length === 0) {
    loadSessions();
  }
}

async function loadSessions() {
  try {
    const data = await store.apiFetch('/api/log/sessions');
    sessions.value = data.sessions || [];
  } catch (e) {
    console.warn('会话列表加载失败:', e);
  }
}

async function openSession(key) {
  try {
    const data = await store.apiFetch(`/api/log/sessions/messages?key=${encodeURIComponent(key)}`);
    sessionMessages.value = data.messages || [];
    currentSession.value = key;
  } catch (e) {
    console.warn('会话消息加载失败:', e);
  }
}

async function removeSession(key) {
  if (!window.confirm(`确认删除会话 ${key}？`)) return;
  try {
    await store.apiFetch(`/api/log/sessions/one?key=${encodeURIComponent(key)}`, {
      method: 'DELETE',
    });
    if (currentSession.value === key) {
      currentSession.value = '';
      sessionMessages.value = [];
    }
    await loadSessions();
  } catch (e) {
    console.warn('会话删除失败:', e);
  }
}

function connectWebSocket() {
  if (!shouldReconnect) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = store.getApiKey() || '';
  const protocols = token ? [`api-key.${token}`] : [];
  socket = new WebSocket(`${proto}//${location.host}/api/ws/log`, protocols);
  socket.onopen = () => {
    wsConnected.value = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };
  socket.onclose = () => {
    wsConnected.value = false;
    if (shouldReconnect) {
      reconnectTimer = setTimeout(connectWebSocket, 3000);
    }
  };
  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data?.type === 'ping') return;
      if (!keyword.value) {
        store.addLog(data);
      }
    } catch (e) {
      console.warn('WebSocket 消息解析失败:', e);
    }
  };
}

async function search() {
  try {
    await store.fetchLogs(keyword.value, 50);
  } catch (e) {
    console.warn('消息搜索失败:', e);
  }
}

function formatTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts);
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

function formatDate(ts) {
  if (!ts) return '-';
  const d = new Date(ts);
  return d.toLocaleString('zh-CN', { hour12: false });
}
</script>

<template>
  <div class="page-header">
    <h1>消息日志</h1>
    <p>实时查看 Bot 接收与发送的消息</p>
  </div>

  <div class="page-body">
    <div class="tabs">
      <button
        class="tab"
        :class="{ active: activeTab === 'messages' }"
        @click="switchTab('messages')"
      >
        消息流
      </button>
      <button
        class="tab"
        :class="{ active: activeTab === 'sessions' }"
        @click="switchTab('sessions')"
      >
        会话记录
      </button>
    </div>

    <div v-show="activeTab === 'messages'" class="card fade-in">
      <div class="card-header">
        <div class="card-title">
          消息流
          <span class="tag tag-status" :class="wsConnected ? 'tag-perm' : 'tag-danger'">
            {{ wsConnected ? '实时推送中' : '推送断开' }}
          </span>
        </div>
        <div class="input-group search-bar">
          <div class="form-group">
            <input
              v-model="keyword"
              type="text"
              placeholder="搜索关键词 / 用户 ID / 群组 ID"
              @keyup.enter="search"
            />
          </div>
          <button class="btn btn-secondary btn-sm" @click="search">搜索</button>
        </div>
      </div>

      <div class="log-container">
        <div v-if="store.logs.length === 0" class="empty-state">
          <div class="icon">✉</div>
          <div>暂无消息记录</div>
        </div>
        <div
          v-for="log in store.logs"
          :key="log.id"
          class="log-item"
          :class="{ user: log.role === 'user', bot: log.role === 'assistant' }"
        >
          <span class="time">{{ formatTime(log.created_at) }}</span>
          <span class="meta">
            {{ log.role === 'user' ? '用户' : 'Bot' }}
            {{ log.group_id ? `群组 ${log.group_id}` : `私聊 ${log.user_id}` }}
          </span>
          <span class="content">{{ log.content }}</span>
        </div>
      </div>
    </div>

    <div v-show="activeTab === 'sessions'" class="card fade-in">
      <div class="card-header">
        <div class="card-title">LLM 会话</div>
        <button class="btn btn-secondary btn-sm" @click="loadSessions">刷新</button>
      </div>

      <!-- 会话列表 -->
      <div v-if="!currentSession">
        <div v-if="sessions.length === 0" class="empty-state">
          <div class="icon">🗨</div>
          <div>暂无会话，与 Bot 对话后自动生成</div>
        </div>
        <div
          v-for="s in sessions"
          :key="s.session_key"
          class="session-item"
          @click="openSession(s.session_key)"
        >
          <div class="session-main">
            <div class="session-title">
              {{ s.group_id ? `群组 ${s.group_id} · 用户 ${s.user_id}` : `私聊 ${s.user_id}` }}
              <span class="tag" style="margin-left: 8px">{{ s.message_count }} 条</span>
            </div>
            <div class="session-meta">
              {{ s.session_key }} · 最后活跃 {{ formatDate(s.last_active) }}
            </div>
          </div>
          <button class="btn btn-danger btn-sm" @click.stop="removeSession(s.session_key)">
            删除
          </button>
        </div>
      </div>

      <!-- 会话详情 -->
      <div v-else>
        <div class="session-detail-header">
          <button class="btn btn-secondary btn-sm" @click="currentSession = ''">← 返回</button>
          <span class="session-detail-key">{{ currentSession }}</span>
        </div>
        <div class="log-container">
          <div v-if="sessionMessages.length === 0" class="empty-state">
            <div class="icon">✉</div>
            <div>该会话暂无消息</div>
          </div>
          <div
            v-for="(m, i) in sessionMessages"
            :key="i"
            class="log-item"
            :class="{ user: m.role === 'user', bot: m.role === 'assistant' }"
          >
            <span class="time">{{ formatTime(m.created_at) }}</span>
            <span class="meta">{{ m.role === 'user' ? '用户' : 'Bot' }}</span>
            <span class="content">{{ m.content }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tag-status {
  margin-left: 10px;
}
.search-bar {
  max-width: 320px;
}
.tabs {
  display: flex;
  gap: 0;
  margin-bottom: 22px;
  border-bottom: 1px solid var(--border-color);
}
.tab {
  padding: 10px 24px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.2s;
  font-family: inherit;
}
.tab:hover {
  color: var(--text-primary);
}
.tab.active {
  color: var(--blue);
  border-bottom-color: var(--blue);
}
.session-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background 0.2s;
}
.session-item:hover {
  background: rgba(255, 255, 255, 0.03);
}
.session-main {
  flex: 1;
  min-width: 0;
}
.session-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}
.session-meta {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 2px;
}
.session-detail-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0 12px;
  border-bottom: 1px solid var(--border-color);
}
.session-detail-key {
  font-size: 13px;
  color: var(--text-muted);
  word-break: break-all;
}
</style>
