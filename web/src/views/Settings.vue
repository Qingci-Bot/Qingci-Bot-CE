<script setup>
import { ref, reactive, onMounted, onUnmounted, watch } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const form = reactive({
  bot: {},
  onebot: {},
  api_key: '',
})
const apiKeyInput = ref('')
const saving = ref(false)
const toast = ref({ show: false, type: 'info', message: '' })
let toastTimer = null

onMounted(() => {
  resetForm()
  apiKeyInput.value = store.getApiKey()
})

onUnmounted(() => {
  if (toastTimer) clearTimeout(toastTimer)
})

watch(() => [store.config.bot, store.config.onebot], () => {
  resetForm()
}, { once: true })

function resetForm() {
  const bot = store.config.bot || {}
  const onebot = store.config.onebot || {}
  Object.assign(form, {
    bot: {
      name: bot.name || 'Qingci-Bot',
      trigger_mode: bot.trigger_mode || 'at',
      trigger_keywords: (bot.trigger_keywords || []).join(', '),
      admin_users: (bot.admin_users || []).join(', '),
      group_blacklist: (bot.group_blacklist || []).join(', '),
      user_blacklist: (bot.user_blacklist || []).join(', '),
    },
    onebot: {
      host: onebot.host || '127.0.0.1',
      port: onebot.port || 3001,
      access_token: onebot.access_token || '',
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

function showToast(type, message) {
  toast.value = { show: true, type, message }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => toast.value.show = false, 4000)
}

function saveApiKey() {
  store.setApiKey(apiKeyInput.value.trim())
  showToast('success', 'API Key 已保存到浏览器')
}

async function saveConfig() {
  saving.value = true
  try {
    const newConfig = JSON.parse(JSON.stringify(store.config))
    newConfig.bot = {
      ...form.bot,
      trigger_keywords: parseList(form.bot.trigger_keywords, false),
      admin_users: parseList(form.bot.admin_users),
      group_blacklist: parseList(form.bot.group_blacklist),
      user_blacklist: parseList(form.bot.user_blacklist),
    }
    newConfig.onebot = {
      ...form.onebot,
      port: Number(form.onebot.port) || 3001,
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
          <input v-model="form.bot.name" type="text" placeholder="Qingci-Bot">
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
          <label>管理员 QQ（逗号分隔）</label>
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

    <div class="card fade-in" style="margin-top: 22px;">
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

    <div class="card fade-in" style="margin-top: 22px;">
      <div class="card-header">
        <div class="card-title">API 鉴权</div>
      </div>
      <div class="form-grid">
        <div class="form-group">
          <label>服务端 API Key（写入 config.yaml）</label>
          <input v-model="form.api_key" type="text" placeholder="留空则不启用鉴权">
        </div>
      </div>
      <div class="hint-text" style="margin-top: 8px;">
        配置后，所有写操作（启停 Bot、修改配置、插件管理）都需要携带此 Key。<br>
        留空表示不启用鉴权（仅本地开发推荐）。
      </div>
      <div class="form-grid" style="margin-top: 16px;">
        <div class="form-group">
          <label>浏览器 API Key（本地存储）</label>
          <div style="display: flex; gap: 8px;">
            <input v-model="apiKeyInput" type="text" placeholder="填写服务端配置的 API Key">
            <button class="btn btn-secondary btn-sm" @click="saveApiKey">保存</button>
          </div>
        </div>
      </div>
    </div>

    <div class="card fade-in" style="margin-top: 22px;">
      <div class="card-header">
        <div class="card-title">保存更改</div>
        <button class="btn btn-primary" :disabled="saving" @click="saveConfig">
          <span>✓</span> {{ saving ? '保存中' : '保存设置' }}
        </button>
      </div>
      <transition name="toast">
        <div v-if="toast.show" class="toast" :class="toast.type">
          {{ toast.message }}
        </div>
      </transition>
    </div>
  </div>
</template>

<style scoped>
.toast-enter-active, .toast-leave-active {
  transition: all 0.3s ease;
}
.toast-enter-from, .toast-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>