import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'

const API = ''

function getApiKey() {
  return localStorage.getItem('qingci_api_key') || ''
}

function setApiKey(key) {
  if (key) {
    localStorage.setItem('qingci_api_key', key)
  } else {
    localStorage.removeItem('qingci_api_key')
  }
}

function authHeaders(extra = {}) {
  const key = getApiKey()
  const headers = { ...extra }
  if (key) {
    headers['X-API-Key'] = key
  }
  return headers
}

const defaultConfig = {
  bot: {
    name: 'Qingci-Bot',
    admin_users: [],
    trigger_mode: 'at',
    trigger_keywords: ['/bot', '/ai'],
    group_blacklist: [],
    user_blacklist: [],
  },
  llm: {
    provider: 'openai',
    api_url: 'https://api.openai.com/v1',
    api_key: '',
    model: 'gpt-4o-mini',
    max_tokens: 2048,
    temperature: 0.7,
    system_prompt: '你是一个友好的 QQ 机器人助手。',
    max_history: 20,
  },
  onebot: {
    host: '127.0.0.1',
    port: 3001,
    access_token: '',
  },
  api_key: '',
}

function deepMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
      if (!target[key] || typeof target[key] !== 'object') {
        target[key] = {}
      }
      deepMerge(target[key], source[key])
    } else {
      target[key] = source[key]
    }
  }
  return target
}

export const useAppStore = defineStore('app', () => {
  const botRunning = ref(false)
  const botConnected = ref(false)
  const plugins = ref([])
  const config = reactive(JSON.parse(JSON.stringify(defaultConfig)))
  const llmPresets = ref({})
  const logs = ref([])
  const loading = ref(false)
  const error = ref('')

  const statusText = computed(() => {
    if (!botRunning.value) return '未启动'
    if (!botConnected.value) return '等待 LLBot 连接'
    return '运行中'
  })

  const statusColor = computed(() => {
    if (!botRunning.value) return '#6b7280'
    if (!botConnected.value) return '#f59e0b'
    return '#10b981'
  })

  async function apiFetch(url, options = {}) {
    try {
      const headers = authHeaders(options.headers || {})
      const res = await fetch(`${API}${url}`, { ...options, headers })
      if (res.status === 401) {
        error.value = 'API Key 鉴权失败，请在设置中配置正确的 API Key'
        throw new Error(error.value)
      }
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `HTTP ${res.status}`)
      }
      return res.status === 204 ? null : await res.json()
    } catch (e) {
      error.value = e.message
      throw e
    }
  }

  async function fetchStatus() {
    try {
      const data = await apiFetch('/api/bot/status')
      botRunning.value = data.running
      botConnected.value = data.connected
      plugins.value = data.plugins || []
      error.value = ''
    } catch (e) {
      botRunning.value = false
      botConnected.value = false
    }
  }

  async function fetchConfig() {
    try {
      const data = await apiFetch('/api/config')
      deepMerge(config, data)
      error.value = ''
    } catch (e) {
      console.warn('fetchConfig failed:', e.message)
    }
  }

  async function fetchLLMPresets() {
    try {
      const data = await apiFetch('/api/config/llm/presets')
      llmPresets.value = data.presets || {}
    } catch (e) {
      console.warn('fetchLLMPresets failed:', e.message)
    }
  }

  async function startBot() {
    loading.value = true
    try {
      await apiFetch('/api/bot/start', { method: 'POST' })
      await fetchStatus()
    } finally {
      loading.value = false
    }
  }

  async function stopBot() {
    loading.value = true
    try {
      await apiFetch('/api/bot/stop', { method: 'POST' })
      await fetchStatus()
    } finally {
      loading.value = false
    }
  }

  async function restartBot() {
    loading.value = true
    try {
      await apiFetch('/api/bot/restart', { method: 'POST' })
      await fetchStatus()
    } finally {
      loading.value = false
    }
  }

  async function saveConfig(newConfig) {
    await apiFetch('/api/config', {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(newConfig),
    })
    await fetchConfig()
  }

  async function testLLM(cfg) {
    return await apiFetch('/api/config/llm/test', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(cfg),
    })
  }

  async function fetchLogs(keyword = '', limit = 50) {
    const params = new URLSearchParams({ keyword, limit: String(limit) })
    logs.value = await apiFetch(`/api/log/messages?${params}`)
  }

  async function fetchMessageCount() {
    return await apiFetch('/api/log/messages/count')
  }

  function addLog(log) {
    logs.value.unshift(log)
    if (logs.value.length > 200) logs.value.pop()
  }

  return {
    botRunning, botConnected, plugins, config, llmPresets, logs, loading, error,
    statusText, statusColor,
    fetchStatus, fetchConfig, fetchLLMPresets, startBot, stopBot, restartBot,
    saveConfig, testLLM, fetchLogs, fetchMessageCount, addLog,
    getApiKey, setApiKey,
  }
})