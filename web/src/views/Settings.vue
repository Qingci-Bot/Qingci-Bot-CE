<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { useToast } from '../composables/useToast'

const store = useAppStore()
const { showToast } = useToast()
const form = reactive({
  bot: {},
  onebot: {},
  platforms: {
    telegram: { name: 'telegram', enabled: false, token: '', poll_interval: 1.0 },
  },
  api_key: '',
})
const apiKeyInput = ref('')
const saving = ref(false)
const showServerKey = ref(false)
const showLocalKey = ref(false)
const showTelegramToken = ref(false)
const backupLoading = ref(false)
const backupResult = ref(null)
const exporting = ref(false)
const auditLogs = ref([])
const auditLoading = ref(false)

onMounted(async () => {
  // 先等待配置加载完成再填充表单，避免用默认值覆盖服务端真实配置
  await store.fetchConfig()
  resetForm()
  apiKeyInput.value = store.getApiKey()
  loadAuditLogs()
})

// 配置加载完成后再同步一次表单（如从保存接口刷新回来）
watch(() => store.configLoaded, (loaded) => {
  if (loaded) resetForm()
})

function resetForm() {
  const bot = store.config.bot || {}
  const onebot = store.config.onebot || {}
  const telegram = (store.config.platforms || {}).telegram || {}
  Object.assign(form, {
    bot: {
      name: bot.name || 'Qingci-Bot CE',
      trigger_mode: bot.trigger_mode || 'at',
      trigger_keywords: (bot.trigger_keywords || []).join(', '),
      super_admin: bot.super_admin ? String(bot.super_admin) : '',
      admin_users: (bot.admin_users || []).join(', '),
      group_blacklist: (bot.group_blacklist || []).join(', '),
      user_blacklist: (bot.user_blacklist || []).join(', '),
    },
    onebot: {
      host: onebot.host || '127.0.0.1',
      port: onebot.port || 3001,
      access_token: onebot.access_token || '',
    },
    platforms: {
      telegram: {
        name: 'telegram',
        enabled: telegram.enabled || false,
        token: telegram.token || '',
        poll_interval: telegram.poll_interval || 1.0,
      },
    },
    api_key: store.config.api_key || '',
  })
}

function parseList(str, asNumber = true) {
  if (!str || !str.trim()) return []
  const arr = str.split(/[,，\n]+/).map(s => s.trim()).filter(Boolean)
  if (asNumber) {
    return arr.map(Number).filter(n => !isNaN(n))
  }
  return arr
}

function saveApiKey() {
  store.setApiKey(apiKeyInput.value.trim())
  showToast('success', 'API Key 已保存到浏览器')
}

async function saveConfig() {
  if (!store.configLoaded) {
    showToast('error', '配置尚未加载完成，无法保存')
    return
  }
  // 服务端 API Key 由非空变空意味着关闭鉴权，需要二次确认
  if (store.config.api_key && !form.api_key.trim()) {
    if (!window.confirm('清空服务端 API Key 将关闭鉴权，确认继续？')) return
  }
  saving.value = true
  try {
    const newConfig = JSON.parse(JSON.stringify(store.config))
    newConfig.bot = {
      ...form.bot,
      super_admin: form.bot.super_admin ? Number(form.bot.super_admin) : null,
      trigger_keywords: parseList(form.bot.trigger_keywords, false),
      admin_users: parseList(form.bot.admin_users),
      group_blacklist: parseList(form.bot.group_blacklist),
      user_blacklist: parseList(form.bot.user_blacklist),
    }
    newConfig.onebot = {
      ...form.onebot,
      port: Number(form.onebot.port) || 3001,
    }
    newConfig.platforms = {
      telegram: {
        ...form.platforms.telegram,
        name: 'telegram',
        poll_interval: Number(form.platforms.telegram.poll_interval) || 1.0,
      },
    }
    newConfig.api_key = form.api_key
    await store.saveConfig(newConfig)
    showToast('success', '系统设置已保存')
  } catch (e) {
    showToast('error', `保存失败：${e.message}`)
  } finally {
    saving.value = false
  }
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function backupDb() {
  backupLoading.value = true
  backupResult.value = null
  try {
    const data = await store.apiFetch('/api/backup/db', { method: 'POST' })
    backupResult.value = data
    showToast('success', `数据库备份成功：${data.filename}`)
  } catch (e) {
    showToast('error', `备份失败：${e.message}`)
  } finally {
    backupLoading.value = false
  }
}

async function exportCsv() {
  exporting.value = true
  try {
    const headers = {}
    const key = store.getApiKey()
    if (key) headers['X-API-Key'] = key
    const res = await fetch('/api/log/messages/export', { headers })
    if (!res.ok) {
      const text = await res.text()
      let msg = text || `HTTP ${res.status}`
      try {
        msg = JSON.parse(text).detail || msg
      } catch (e) {
        // 非 JSON 响应体，直接使用原文本
      }
      // 401 时跳转登录页，与 apiFetch 的鉴权失败行为保持一致
      if (res.status === 401 && window.location.hash !== '#/login') {
        window.location.hash = '#/login'
      }
      throw new Error(msg)
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'messages.csv'
    a.click()
    URL.revokeObjectURL(url)
    showToast('success', '消息 CSV 已开始下载')
  } catch (e) {
    showToast('error', `导出失败：${e.message}`)
  } finally {
    exporting.value = false
  }
}

async function loadAuditLogs() {
  auditLoading.value = true
  try {
    const data = await store.apiFetch('/api/audit/logs?limit=100')
    auditLogs.value = data.logs || []
  } catch (e) {
    auditLogs.value = []
    showToast('error', `审计日志加载失败：${e.message}`)
  } finally {
    auditLoading.value = false
  }
}

// 高级配置：JSON 编辑器（借鉴 AstrBot 可视化配置 + 代码编辑双模式）
const configJson = ref('')
const jsonSaving = ref(false)

function loadConfigJson() {
  configJson.value = JSON.stringify(store.config, null, 2)
}

async function saveConfigJson() {
  let data
  try {
    data = JSON.parse(configJson.value)
  } catch (e) {
    showToast('error', `JSON 格式错误：${e.message}`)
    return
  }
  jsonSaving.value = true
  try {
    await store.saveConfig(data)
    showToast('success', '配置已保存')
  } catch (e) {
    showToast('error', `保存失败：${e.message}`)
  } finally {
    jsonSaving.value = false
  }
}
</script>

<template>
  <div class="page-header">
    <h1>系统设置</h1>
    <p>配置 Bot 行为、触发条件与 OneBot 连接参数</p>
  </div>

  <div class="page-body">
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">Bot 行为</div>
      </div>
      <div class="form-grid">
        <div class="form-group">
          <label>Bot 名称</label>
          <input v-model="form.bot.name" type="text" placeholder="Qingci-Bot CE">
        </div>
        <div class="form-group">
          <label>触发模式</label>
          <select v-model="form.bot.trigger_mode">
            <option value="always">所有消息都回复</option>
            <option value="at">被 @ 时回复</option>
            <option value="keyword">关键词触发</option>
          </select>
        </div>
        <div class="form-group">
          <label>触发关键词（逗号分隔）</label>
          <input v-model="form.bot.trigger_keywords" type="text" placeholder="/bot, /ai">
        </div>
        <div class="form-group">
          <label>超级管理员 QQ（唯一，拥有全部权限）</label>
          <input v-model="form.bot.super_admin" type="text" placeholder="123456789">
        </div>
        <div class="form-group">
          <label>普通管理员 QQ（逗号分隔，可多个）</label>
          <input v-model="form.bot.admin_users" type="text" placeholder="123456789">
        </div>
        <div class="form-group">
          <label>群黑名单（逗号分隔）</label>
          <input v-model="form.bot.group_blacklist" type="text" placeholder="123456789">
        </div>
        <div class="form-group">
          <label>用户黑名单（逗号分隔）</label>
          <input v-model="form.bot.user_blacklist" type="text" placeholder="123456789">
        </div>
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">OneBot 连接</div>
      </div>
      <div class="form-grid">
        <div class="form-group">
          <label>监听地址</label>
          <input v-model="form.onebot.host" type="text" placeholder="127.0.0.1">
        </div>
        <div class="form-group">
          <label>监听端口</label>
          <input v-model.number="form.onebot.port" type="number" placeholder="3001">
        </div>
        <div class="form-group">
          <label>Access Token（可选）</label>
          <input v-model="form.onebot.access_token" type="password" placeholder="留空表示不校验">
        </div>
      </div>
      <div class="hint-text" style="margin-top: 16px;">
        <strong>反向 WebSocket 地址：</strong>ws://{{ form.onebot.host || '127.0.0.1' }}:{{ form.onebot.port || 3001 }}<br>
        修改端口后需要重启 Bot 才能生效。
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">平台适配器</div>
      </div>
      <div class="platform-cfg">
        <div class="platform-cfg-head">
          <div class="form-group" style="margin: 0; flex: 1;">
            <label>Telegram（Bot API 长轮询）</label>
            <div class="switch-row">
              <label class="switch">
                <input type="checkbox" v-model="form.platforms.telegram.enabled">
                <span class="slider"></span>
              </label>
              <span class="text-muted" style="font-size: 13px;">
                {{ form.platforms.telegram.enabled ? '已启用' : '已停用' }}
              </span>
            </div>
          </div>
        </div>
        <div class="form-grid">
          <div class="form-group">
            <label>Bot Token</label>
            <input
              v-model="form.platforms.telegram.token"
              :type="showTelegramToken ? 'text' : 'password'"
              placeholder="123456:ABC-DEF...（@BotFather 获取）"
            >
          </div>
          <div class="form-group">
            <label>轮询间隔（秒）</label>
            <input v-model.number="form.platforms.telegram.poll_interval" type="number" min="0.5" step="0.5" placeholder="1.0">
          </div>
        </div>
        <div class="form-group" style="margin-top: 8px;">
          <button class="btn btn-secondary btn-sm" @click="showTelegramToken = !showTelegramToken">
            {{ showTelegramToken ? '隐藏' : '显示' }} Token
          </button>
        </div>
      </div>
      <div class="hint-text" style="margin-top: 16px;">
        启用后以 Telegram Bot API 长轮询接入，现有插件/命令零改动可用；回复自动路由到对应平台。
        <strong>修改后需要重启 Bot 才能生效。</strong>Token 已配置时显示为 <code>***</code>，留空保存不会清空原值。
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">API 鉴权</div>
      </div>
      <div class="form-grid">
        <div class="form-group">
          <label>服务端 API Key（写入 config.yaml）</label>
          <div class="input-group">
            <input v-model="form.api_key" :type="showServerKey ? 'text' : 'password'" placeholder="留空则不启用鉴权">
            <button class="btn btn-secondary btn-sm" @click="showServerKey = !showServerKey">
              {{ showServerKey ? '隐藏' : '显示' }}
            </button>
          </div>
        </div>
      </div>
      <div class="hint-text" style="margin-top: 8px;">
        配置后，所有写操作（启停 Bot、修改配置、插件管理）都需要携带此 Key。<br>
        留空表示不启用鉴权（仅本地开发推荐）。
      </div>
      <div class="form-grid" style="margin-top: 16px;">
        <div class="form-group">
          <label>浏览器 API Key（本地存储）</label>
          <div class="input-group">
            <input v-model="apiKeyInput" :type="showLocalKey ? 'text' : 'password'" placeholder="填写服务端配置的 API Key">
            <button class="btn btn-secondary btn-sm" @click="showLocalKey = !showLocalKey">
              {{ showLocalKey ? '隐藏' : '显示' }}
            </button>
            <button class="btn btn-secondary btn-sm" @click="saveApiKey">保存</button>
          </div>
        </div>
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">数据管理</div>
        <div class="action-bar">
          <button class="btn btn-secondary" :disabled="backupLoading" @click="backupDb">
            <span style="display: inline-block" :class="{ spin: backupLoading }">◍</span>
            {{ backupLoading ? '备份中' : '立即备份' }}
          </button>
          <button class="btn btn-secondary" :disabled="exporting" @click="exportCsv">
            <span style="display: inline-block" :class="{ spin: exporting }">⇩</span>
            {{ exporting ? '导出中' : '导出消息 CSV' }}
          </button>
        </div>
      </div>
      <div v-if="backupResult" class="status-bar success">
        备份完成：{{ backupResult.filename }}（{{ formatSize(backupResult.size) }}），保存在服务端 data/backups/ 目录
      </div>
      <div class="hint-text" style="margin-top: 8px;">
        备份使用 SQLite 在线备份 API，保留最近 10 份；CSV 导出包含全部消息记录（utf-8-sig 编码，Excel 可直接打开）。
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">审计日志</div>
        <button class="btn btn-secondary btn-sm" :disabled="auditLoading" @click="loadAuditLogs">
          <span style="display: inline-block" :class="{ spin: auditLoading }">↻</span> 刷新
        </button>
      </div>
      <div v-if="auditLogs.length === 0" class="empty-state">
        <div class="icon">✦</div>
        <div>暂无审计日志</div>
      </div>
      <div v-else style="max-height: 420px; overflow-y: auto;">
        <table class="table">
          <thead>
            <tr>
              <th class="audit-time">时间</th>
              <th class="audit-action">动作</th>
              <th class="audit-detail">详情</th>
              <th class="audit-ip">来源 IP</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in auditLogs" :key="log.id">
              <td class="audit-time">{{ log.created_at }}</td>
              <td><span class="tag tag-blue">{{ log.action }}</span></td>
              <td class="audit-detail">{{ log.detail || '-' }}</td>
              <td class="audit-ip">{{ log.client_ip || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">高级配置（JSON）</div>
        <div class="action-bar">
          <button class="btn btn-secondary btn-sm" @click="loadConfigJson">加载当前配置</button>
          <button class="btn btn-primary btn-sm" :disabled="jsonSaving" @click="saveConfigJson">
            {{ jsonSaving ? '保存中' : '保存 JSON' }}
          </button>
        </div>
      </div>
      <div class="hint-text" style="margin-bottom: 12px;">
        直接编辑完整配置（JSON 格式）。敏感字段（api_key / access_token）显示为
        <code>***</code>，保存时后端自动过滤占位符、保留原值。修改需符合配置模型约束。
      </div>
      <textarea v-model="configJson" class="json-editor" spellcheck="false" placeholder="点击「加载当前配置」填充"></textarea>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">保存更改</div>
        <button class="btn btn-primary" :disabled="saving" @click="saveConfig">
          <span>✓</span> {{ saving ? '保存中' : '保存设置' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 平台适配器卡片 */
.platform-cfg {
  padding: 16px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.02);
}
.platform-cfg-head {
  display: flex;
  align-items: center;
  margin-bottom: 14px;
}
.switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.json-editor {
  width: 100%;
  min-height: 360px;
  resize: vertical;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.6);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  box-sizing: border-box;
}
.json-editor:focus {
  outline: none;
  border-color: var(--blue);
}

/* 审计日志列样式（收敛内联样式） */
.audit-time { width: 170px; }
.audit-action { width: 170px; }
.audit-ip { width: 120px; }
td.audit-time {
  font-family: var(--font-mono);
  color: var(--text-muted);
  white-space: nowrap;
}
td.audit-ip { font-family: var(--font-mono); }
td.audit-detail { word-break: break-all; }

/* API Key 输入行：输入框撑满剩余宽度 */
.input-group input { flex: 1; min-width: 0; }
</style>