<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { invalidateWizardStatusCache } from '../router'

const router = useRouter()

const step = ref(1)
const totalSteps = 3
const loading = ref(false)
const error = ref('')
const testResult = ref(null)

// Step 1: Provider
const provider = ref('deepseek')
const providers = [
  { value: 'deepseek', label: 'DeepSeek', desc: '国产高性价比，推荐' },
  { value: 'openai', label: 'OpenAI', desc: 'GPT 系列模型' },
  { value: 'siliconflow', label: 'SiliconFlow', desc: '国产模型聚合平台' },
  { value: 'ollama', label: 'Ollama', desc: '本地部署，无需 API Key' },
  { value: 'claude', label: 'Claude', desc: 'Anthropic 系列模型' },
  { value: 'gemini', label: 'Gemini', desc: 'Google 系列模型' },
  { value: 'custom', label: '自定义', desc: '任意 OpenAI 兼容 API' },
]

// Step 2: API Key
const apiKey = ref('')
const apiUrl = ref('')
const model = ref('')
const showAdvanced = ref(false)

// Step 3: Admin
const adminQQ = ref('')
const onebotPort = ref('3001')

function nextStep() {
  if (step.value === 1 && !provider.value) return
  if (step.value === 2) {
    if (provider.value !== 'ollama' && !apiKey.value.trim()) {
      error.value = '请填写 API Key'
      return
    }
  }
  error.value = ''
  if (step.value < totalSteps) {
    step.value++
  }
}

function prevStep() {
  if (step.value > 1) {
    step.value--
    error.value = ''
  }
}

function onProviderChange() {
  error.value = ''
  testResult.value = null
}

async function testConnection() {
  loading.value = true
  error.value = ''
  testResult.value = null
  try {
    const body = { provider: provider.value }
    if (apiKey.value.trim()) body.api_key = apiKey.value.trim()
    if (apiUrl.value.trim()) body.api_url = apiUrl.value.trim()
    if (model.value.trim()) body.model = model.value.trim()

    const res = await fetch('/api/config/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (res.ok && data.available) {
      testResult.value = { ok: true, msg: '连接成功' }
    } else {
      testResult.value = { ok: false, msg: data.message || data.detail || '连接失败' }
    }
  } catch (e) {
    testResult.value = { ok: false, msg: '网络错误: ' + e.message }
  } finally {
    loading.value = false
  }
}

async function completeSetup() {
  loading.value = true
  error.value = ''
  try {
    const body = {
      provider: provider.value,
      api_key: apiKey.value.trim(),
      api_url: apiUrl.value.trim() || undefined,
      model: model.value.trim() || undefined,
      admin_qq: adminQQ.value.trim() || undefined,
      onebot_port: onebotPort.value.trim() || undefined,
    }
    const res = await fetch('/api/config/wizard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (!res.ok) {
      error.value = data.detail || '配置失败'
      loading.value = false
      return
    }
    // 配置完成，跳转首页
    invalidateWizardStatusCache()
    router.push('/')
  } catch (e) {
    error.value = '网络错误: ' + e.message
    loading.value = false
  }
}

async function skipSetup() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/config/wizard/skip', { method: 'POST' })
    if (!res.ok) {
      const data = await res.json()
      error.value = data.detail || '跳过失败'
      loading.value = false
      return
    }
    invalidateWizardStatusCache()
    router.push('/')
  } catch (e) {
    error.value = '网络错误: ' + e.message
    loading.value = false
  }
}

const canTest = computed(() => {
  if (provider.value === 'ollama') return true
  return apiKey.value.trim().length > 0
})

const progressPercent = computed(() => Math.round((step.value / totalSteps) * 100))
</script>

<template>
  <div class="wizard-page">
    <div class="wizard-card">
      <div class="wizard-header">
        <div class="wizard-logo">Qingci-Bot CE</div>
        <div class="wizard-subtitle">首次配置引导</div>
        <div class="wizard-progress">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
          </div>
          <div class="progress-text">步骤 {{ step }} / {{ totalSteps }}</div>
        </div>
      </div>

      <!-- Step 1: 选择提供商 -->
      <div v-if="step === 1" class="wizard-step fade-in">
        <div class="step-title">选择 LLM 提供商</div>
        <div class="step-desc">选择你要使用的大模型服务商</div>
        <div class="provider-grid">
          <div
            v-for="p in providers"
            :key="p.value"
            class="provider-card"
            :class="{ selected: provider === p.value }"
            @click="provider = p.value; onProviderChange()"
          >
            <div class="provider-name">{{ p.label }}</div>
            <div class="provider-desc">{{ p.desc }}</div>
          </div>
        </div>
      </div>

      <!-- Step 2: 填写 API Key -->
      <div v-if="step === 2" class="wizard-step fade-in">
        <div class="step-title">配置 API 连接</div>
        <div class="step-desc">
          {{ provider === 'ollama' ? 'Ollama 本地服务无需 API Key，确认地址后可直接测试连接' : '填写你的 API Key 以连接模型服务' }}
        </div>

        <div v-if="provider !== 'ollama'" class="form-group">
          <label class="form-label">API Key</label>
          <input
            v-model="apiKey"
            type="password"
            class="form-input"
            placeholder="sk-..."
            autocomplete="off"
          />
        </div>

        <div class="form-group">
          <label class="form-label">API 地址 <span class="optional">(可选，默认使用官方地址)</span></label>
          <input
            v-model="apiUrl"
            type="text"
            class="form-input"
            :placeholder="provider === 'deepseek' ? 'https://api.deepseek.com/v1' : ''"
          />
        </div>

        <div class="form-group">
          <label class="form-label">模型名称 <span class="optional">(可选，默认使用推荐模型)</span></label>
          <input
            v-model="model"
            type="text"
            class="form-input"
            :placeholder="provider === 'deepseek' ? 'deepseek-chat' : ''"
          />
        </div>

        <div class="test-section">
          <button class="btn btn-secondary btn-sm" :disabled="loading || !canTest" @click="testConnection">
            <span v-if="loading" class="spin">↻</span>
            <span v-else>⚡</span>
            测试连接
          </button>
          <span v-if="testResult" class="test-result" :class="{ ok: testResult.ok, fail: !testResult.ok }">
            {{ testResult.ok ? '✓' : '✗' }} {{ testResult.msg }}
          </span>
        </div>
      </div>

      <!-- Step 3: 管理员信息 -->
      <div v-if="step === 3" class="wizard-step fade-in">
        <div class="step-title">最后一步</div>
        <div class="step-desc">设置管理员信息与 OneBot 端口</div>

        <div class="form-group">
          <label class="form-label">超级管理员 QQ <span class="optional">(可选，唯一，拥有全部权限)</span></label>
          <input
            v-model="adminQQ"
            type="text"
            class="form-input"
            placeholder="例如：123456789"
          />
        </div>

        <div class="form-group">
          <label class="form-label">OneBot 端口 <span class="optional">(LLBot 连接 ws://127.0.0.1:此端口/ws)</span></label>
          <input
            v-model="onebotPort"
            type="text"
            class="form-input"
            placeholder="3001"
          />
        </div>

        <div class="summary-card">
          <div class="summary-title">配置摘要</div>
          <div class="summary-row">
            <span>提供商</span>
            <span>{{ providers.find(p => p.value === provider)?.label || provider }}</span>
          </div>
          <div class="summary-row">
            <span>API Key</span>
            <span>{{ apiKey ? '****' + apiKey.slice(-4) : '(未填写)' }}</span>
          </div>
          <div class="summary-row" v-if="adminQQ">
            <span>超级管理员 QQ</span>
            <span>{{ adminQQ }}</span>
          </div>
          <div class="summary-row">
            <span>OneBot 端口</span>
            <span>{{ onebotPort }}</span>
          </div>
        </div>
      </div>

      <!-- Error -->
      <div v-if="error" class="error-msg">{{ error }}</div>

      <!-- Navigation -->
      <div class="wizard-nav">
        <button v-if="step > 1" class="btn btn-secondary" @click="prevStep">上一步</button>
        <button v-else class="btn btn-ghost" :disabled="loading" @click="skipSetup">跳过</button>
        <div class="spacer"></div>
        <button
          v-if="step < totalSteps"
          class="btn btn-primary"
          @click="nextStep"
        >
          下一步
        </button>
        <button
          v-else
          class="btn btn-accent"
          :disabled="loading"
          @click="completeSetup"
        >
          <span v-if="loading" class="spin">↻</span>
          完成配置
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wizard-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: var(--bg-primary);
  padding: 24px;
}

.wizard-card {
  width: 100%;
  max-width: 560px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 36px 32px;
}

.wizard-header {
  text-align: center;
  margin-bottom: 28px;
}

.wizard-logo {
  font-size: 24px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 2px;
}

.wizard-subtitle {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 4px;
}

.wizard-progress {
  margin-top: 20px;
}

.progress-bar {
  height: 4px;
  background: rgba(15, 23, 42, 0.5);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 6px;
}

/* Step */
.wizard-step {
  min-height: 200px;
}

.step-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.step-desc {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 20px;
  line-height: 1.6;
}

/* Provider Grid */
.provider-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

@media (max-width: 480px) {
  .provider-grid {
    grid-template-columns: 1fr;
  }
}

.provider-card {
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  background: rgba(15, 23, 42, 0.3);
}

.provider-card:hover {
  border-color: var(--blue);
  background: rgba(56, 189, 248, 0.05);
}

.provider-card.selected {
  border-color: var(--accent);
  background: rgba(251, 191, 36, 0.08);
  box-shadow: 0 0 12px var(--accent-glow);
}

.provider-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.provider-desc {
  font-size: 11px;
  color: var(--text-muted);
}

/* Form */
.form-group {
  margin-bottom: 16px;
}

.form-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.optional {
  font-weight: 400;
  color: var(--text-muted);
  font-size: 11px;
}

.form-input {
  width: 100%;
  padding: 10px 14px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 14px;
  font-family: var(--font-mono);
  outline: none;
  transition: border-color 0.2s;
  box-sizing: border-box;
}

.form-input:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.15);
}

/* Test */
.test-section {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
}

.test-result {
  font-size: 13px;
  font-weight: 500;
}

.test-result.ok {
  color: #4ade80;
}

.test-result.fail {
  color: #f87171;
}

/* Summary */
.summary-card {
  margin-top: 20px;
  padding: 16px;
  background: rgba(15, 23, 42, 0.4);
  border: 1px solid var(--border-color);
  border-radius: 8px;
}

.summary-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 10px;
}

.summary-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 13px;
}

.summary-row span:first-child {
  color: var(--text-muted);
}

.summary-row span:last-child {
  color: var(--text-primary);
  font-weight: 500;
  font-family: var(--font-mono);
  font-size: 12px;
}

/* Error */
.error-msg {
  margin-top: 16px;
  padding: 10px 14px;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: 6px;
  color: #f87171;
  font-size: 13px;
}

/* Nav */
.wizard-nav {
  display: flex;
  align-items: center;
  margin-top: 24px;
  gap: 10px;
}

.spacer {
  flex: 1;
}

.btn-accent {
  background: linear-gradient(135deg, var(--accent), #f59e0b);
  color: #0f172a;
  border: none;
  padding: 10px 24px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-accent:hover {
  box-shadow: 0 0 16px var(--accent-glow);
  transform: translateY(-1px);
}

.btn-accent:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.btn-ghost {
  background: transparent;
  color: var(--text-muted);
  border: 1px solid var(--border-color);
  padding: 10px 20px;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-ghost:hover {
  color: var(--text-secondary);
  border-color: var(--text-muted);
}

.spin {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.fade-in {
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>