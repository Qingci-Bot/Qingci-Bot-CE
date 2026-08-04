<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const keyword = ref('')
const wsConnected = ref(false)
let socket = null
let shouldReconnect = true
let reconnectTimer = null

onMounted(() => {
  store.fetchLogs('', 50).catch((e) => console.warn('初始消息日志加载失败:', e))
  connectWebSocket()
})

onUnmounted(() => {
  shouldReconnect = false
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (socket) socket.close()
})

function connectWebSocket() {
  if (!shouldReconnect) return
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = encodeURIComponent(store.getApiKey() || '')
  socket = new WebSocket(`${proto}//${location.host}/api/ws/log?token=${token}`)
  socket.onopen = () => {
    wsConnected.value = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }
  socket.onclose = () => {
    wsConnected.value = false
    if (shouldReconnect) {
      reconnectTimer = setTimeout(connectWebSocket, 3000)
    }
  }
  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data?.type === 'ping') return
      if (!keyword.value) {
        store.addLog(data)
      }
    } catch (e) {
      console.warn('WebSocket 消息解析失败:', e)
    }
  }
}

async function search() {
  try {
    await store.fetchLogs(keyword.value, 50)
  } catch (e) {
    console.warn('消息搜索失败:', e)
  }
}

function formatTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="page-header">
    <h1>消息日志</h1>
    <p>实时查看 Bot 接收与发送的消息</p>
  </div>

  <div class="page-body">
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">
          消息流
          <span class="tag" :class="wsConnected ? 'tag-success' : 'tag-danger'" style="margin-left: 10px;">
            {{ wsConnected ? '实时推送中' : '推送断开' }}
          </span>
        </div>
        <div class="input-group" style="max-width: 320px;">
          <div class="form-group" style="flex: 1; margin-bottom: 0;">
            <input v-model="keyword" type="text" placeholder="搜索关键词 / QQ / 群号" @keyup.enter="search">
          </div>
          <button class="btn btn-secondary btn-sm" @click="search">搜索</button>
        </div>
      </div>

      <div class="log-container">
        <div v-if="store.logs.length === 0" class="empty-state" style="padding: 30px;">
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
            {{ log.group_id ? `群 ${log.group_id}` : `私聊 ${log.user_id}` }}
          </span>
          <span class="content">{{ log.content }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tag-success { background: rgba(16, 185, 129, 0.1); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.2); }
</style>
