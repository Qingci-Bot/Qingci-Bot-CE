import { defineStore } from 'pinia';
import { ref, computed, reactive } from 'vue';
import { authHeaders, getApiKey, request, setApiKey } from '../api/request';

// 默认值逐项与后端 bot/config.py 的模型定义保持一致
const defaultConfig = {
  bot: {
    name: 'Qingci-Bot CE',
    admin_users: [],
    trigger_mode: 'at',
    trigger_keywords: ['/bot', '/ai'],
    group_blacklist: [],
    user_blacklist: [],
    log_json: false,
  },
  llm: {
    provider: 'openai',
    api_url: 'https://api.openai.com/v1',
    api_key: '',
    model: 'gpt-4o-mini',
    max_tokens: 2048,
    temperature: 0.7,
    system_prompt: '你是一个友好、乐于助人的机器人助手。请用简洁、自然的中文回复。',
    personas: [],
    default_persona: '',
    max_history: 20,
    max_context_tokens: 8192,
    timeout: 60,
    num_retries: 2,
    enable_summary: false,
    enable_tools: false,
    max_tool_rounds: 5,
    mcp_servers: [],
  },
  onebot: {
    enabled: true,
    host: '127.0.0.1',
    port: 3001,
    access_token: '',
  },
  platforms: {
    telegram: {
      name: 'telegram',
      enabled: false,
      token: '',
      poll_interval: 1.0,
    },
  },
  rate_limit: {
    enabled: false,
    daily_limit: 50,
    cooldown_seconds: 10,
  },
  filter: {
    enabled: false,
    words_file: 'data/sensitive_words.txt',
    exempt_admins: true,
  },
  scheduler: {
    enabled: true,
  },
  alert: {
    enabled: false,
    error_threshold: 5,
    cooldown_minutes: 10,
  },
  image: {
    enabled: false,
    model: 'dall-e-3',
    api_url: '',
    api_key: '',
  },
  rag: {
    enabled: false,
    embedding_model: '',
    top_k: 3,
    knowledge_dir: 'data/knowledge',
    chunk_size: 400,
    chunk_overlap: 50,
    max_inject_chars: 800,
  },
  session_summary: {
    enabled: false,
    keep_recent_turns: 3,
    max_messages: 20,
    max_tokens: 4096,
    summary_max_tokens: 512,
  },
  log: {
    usage_tracking: true,
    run_log_enabled: true,
  },
  api_key: '',
};

function deepMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
      if (!target[key] || typeof target[key] !== 'object') {
        target[key] = {};
      }
      deepMerge(target[key], source[key]);
    } else {
      target[key] = source[key];
    }
  }
  return target;
}

export const useAppStore = defineStore('app', () => {
  const botRunning = ref(false);
  const botConnected = ref(false);
  const platforms = ref([]);
  const plugins = ref([]);
  const config = reactive(JSON.parse(JSON.stringify(defaultConfig)));
  const llmPresets = ref({});
  const logs = ref([]);
  const loading = ref(false);
  const error = ref('');
  const configLoaded = ref(false);
  const instances = ref([]);
  const appVersion = ref('');

  const statusText = computed(() => {
    if (!botRunning.value) return '未启动';
    if (!botConnected.value) return '等待协议端连接';
    return '运行中';
  });

  const statusColor = computed(() => {
    if (!botRunning.value) return '#6b7280';
    if (!botConnected.value) return '#f59e0b';
    return '#10b981';
  });

  async function apiFetch(url, options = {}) {
    // 统一由独立 API 层处理（鉴权头注入 / JSON 解析 / 401 跳转），
    // 此处仅保留"错误写入 store.error 供组件提示"的行为
    try {
      return await request(url, options);
    } catch (e) {
      if (!error.value) error.value = e.message;
      throw e;
    }
  }

  async function fetchStatus() {
    try {
      const data = await apiFetch('/api/bot/status');
      botRunning.value = data.running;
      botConnected.value = data.connected;
      appVersion.value = data.version || '';
      platforms.value = data.platforms || [];
      plugins.value = data.plugins || [];
      error.value = '';
      return true;
    } catch (e) {
      // 拉取失败时保留上次 botRunning/botConnected 状态，避免轮询抖动误报"未启动"；
      // 错误信息已由 apiFetch 写入 error.value
      return false;
    }
  }

  async function fetchConfig() {
    try {
      const data = await apiFetch('/api/config');
      deepMerge(config, data);
      configLoaded.value = true;
      error.value = '';
    } catch (e) {
      console.warn('fetchConfig failed:', e.message);
    }
  }

  async function fetchLLMPresets() {
    try {
      const data = await apiFetch('/api/config/llm/presets');
      llmPresets.value = data.presets || {};
    } catch (e) {
      console.warn('fetchLLMPresets failed:', e.message);
    }
  }

  async function startBot() {
    loading.value = true;
    try {
      await apiFetch('/api/bot/start', { method: 'POST' });
      await fetchStatus();
    } finally {
      loading.value = false;
    }
  }

  async function stopBot() {
    loading.value = true;
    try {
      await apiFetch('/api/bot/stop', { method: 'POST' });
      await fetchStatus();
    } finally {
      loading.value = false;
    }
  }

  async function restartBot() {
    loading.value = true;
    try {
      await apiFetch('/api/bot/restart', { method: 'POST' });
      await fetchStatus();
    } finally {
      loading.value = false;
    }
  }

  async function saveConfig(newConfig) {
    await apiFetch('/api/config', {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(newConfig),
    });
    await fetchConfig();
  }

  async function testLLM(cfg) {
    return await apiFetch('/api/config/llm/test', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(cfg),
    });
  }

  async function fetchLLMModels(cfg) {
    return await apiFetch('/api/config/llm/models', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(cfg),
    });
  }

  async function fetchLogs(keyword = '', limit = 50) {
    const params = new URLSearchParams({ keyword, limit: String(limit) });
    logs.value = await apiFetch(`/api/log/messages?${params}`);
  }

  async function fetchMessageCount() {
    return await apiFetch('/api/log/messages/count');
  }

  function addLog(log) {
    logs.value.unshift(log);
    if (logs.value.length > 200) logs.value.pop();
  }

  // ---- 实例管理 ----

  // 当前运行中的实例（用于仪表盘等页面按平台展示提示）
  const currentInstance = computed(() => instances.value.find((i) => i.running) || null);

  async function fetchInstances() {
    try {
      instances.value = (await apiFetch('/api/instances')) || [];
      error.value = '';
    } catch (e) {
      console.warn('fetchInstances failed:', e.message);
    }
  }

  async function createInstance(payload) {
    const inst = await apiFetch('/api/instances', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    await fetchInstances();
    return inst;
  }

  async function deleteInstance(name) {
    await apiFetch(`/api/instances/${encodeURIComponent(name)}`, { method: 'DELETE' });
    await fetchInstances();
  }

  async function renameInstance(name, newName) {
    const inst = await apiFetch(`/api/instances/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ new_name: newName }),
    });
    await fetchInstances();
    return inst;
  }

  function switchInstance(name) {
    // 触发后端重启进程到目标实例；当前连接将断开，页面随之刷新
    apiFetch(`/api/instances/${encodeURIComponent(name)}/start`, { method: 'POST' }).catch(
      () => {},
    );
  }

  return {
    botRunning,
    botConnected,
    platforms,
    plugins,
    config,
    llmPresets,
    logs,
    loading,
    error,
    configLoaded,
    instances,
    appVersion,
    statusText,
    statusColor,
    currentInstance,
    fetchStatus,
    fetchConfig,
    fetchLLMPresets,
    startBot,
    stopBot,
    restartBot,
    saveConfig,
    testLLM,
    fetchLLMModels,
    fetchLogs,
    fetchMessageCount,
    addLog,
    fetchInstances,
    createInstance,
    deleteInstance,
    renameInstance,
    switchInstance,
    apiFetch,
    getApiKey,
    setApiKey,
  };
});
