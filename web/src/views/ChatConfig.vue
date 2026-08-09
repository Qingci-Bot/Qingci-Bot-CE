<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { useToast } from '../composables/useToast'

const store = useAppStore()
const { toast, showToast } = useToast()
const form = reactive({})
const testing = ref(false)
const saving = ref(false)

// 提供商列表，从后端 presets 动态生成
const providerOptions = computed(() => {
  const presets = store.llmPresets
  const labels = {
    openai: 'OpenAI',
    deepseek: 'DeepSeek',
    ollama: 'Ollama (本地)',
    siliconflow: 'SiliconFlow',
    claude: 'Claude (Anthropic)',
    gemini: 'Gemini (Google)',
    custom: '自定义',
  }
  const keys = Object.keys(presets).length ? Object.keys(presets) : ['openai', 'deepseek', 'ollama', 'custom']
  return keys.map(k => ({ value: k, label: labels[k] || k }))
})

onMounted(async () => {
  // 先等待配置加载完成再填充表单，避免用默认值覆盖服务端真实配置
  await store.fetchConfig()
  resetForm()
  await store.fetchLLMPresets()
})

// 配置加载完成后再同步一次表单（如从保存接口刷新回来）
watch(() => store.configLoaded, (loaded) => {
  if (loaded) resetForm()
})

function resetForm() {
  const llm = store.config.llm || {}
  Object.assign(form, {
    provider: llm.provider ?? 'openai',
    api_url: llm.api_url ?? 'https://api.openai.com/v1',
    api_key: llm.api_key ?? '',
    model: llm.model ?? 'gpt-4o-mini',
    max_tokens: llm.max_tokens ?? 2048,
    temperature: llm.temperature ?? 0.7,
    system_prompt: llm.system_prompt ?? '',
    personas: Array.isArray(llm.personas)
      ? llm.personas.map(p => ({ ...p }))
      : [],
    default_persona: llm.default_persona ?? '',
    mcp_servers: Array.isArray(llm.mcp_servers)
      ? llm.mcp_servers.map(s => ({
          name: s.name ?? '',
          command: s.command ?? '',
          args: Array.isArray(s.args) ? s.args.join(', ') : '',
          url: s.url ?? '',
        }))
      : [],
    max_history: llm.max_history ?? 20,
  })
}

// 人格管理
function addPersona() {
  form.personas.push({ name: '', description: '', system_prompt: '' })
}
function removePersona(idx) {
  const removed = form.personas[idx]
  if (removed && form.default_persona === removed.name) {
    form.default_persona = ''
  }
  form.personas.splice(idx, 1)
}
function toggleDefaultPersona(name) {
  form.default_persona = form.default_persona === name ? '' : name
}

// MCP 服务器管理
function addMcpServer() {
  form.mcp_servers.push({ name: '', command: '', args: '', url: '' })
}
function removeMcpServer(idx) {
  form.mcp_servers.splice(idx, 1)
}

// provider 切换时自动填充 api_url 和 model
// 与后端 LLMConfig.apply_provider_preset 逻辑一致：仅当当前值为空或等于
// 当前 provider 自己的预设值时才填充，用户自定义值保留不变
function onProviderChange() {
  const preset = store.llmPresets[form.provider]
  if (!preset) return
  if (form.provider === 'custom') return  // custom 完全由用户管理
  if (!form.api_url || form.api_url === preset.api_url) {
    form.api_url = preset.api_url
  }
  if (!form.model || form.model === preset.model) {
    form.model = preset.model
  }
}

async function testConnection() {
  testing.value = true
  try {
    const res = await store.testLLM({ ...form })
    showToast(res.available ? 'success' : 'error', res.available ? 'LLM 连接测试通过' : 'LLM 连接测试失败')
  } catch (e) {
    showToast('error', `测试失败：${e.message}`)
  } finally {
    testing.value = false
  }
}

async function saveConfig() {
  if (!store.configLoaded) {
    showToast('error', '配置尚未加载完成，无法保存')
    return
  }
  saving.value = true
  try {
    const newConfig = JSON.parse(JSON.stringify(store.config))
    // 表单中的 mcp args 为逗号分隔字符串，提交前转为数组
    newConfig.llm = {
      ...form,
      mcp_servers: (form.mcp_servers || []).map(s => ({
        name: s.name,
        command: s.command,
        args: typeof s.args === 'string'
          ? s.args.split(/[,，\s]+/).map(x => x.trim()).filter(Boolean)
          : (s.args || []),
        url: s.url,
      })),
    }
    await store.saveConfig(newConfig)
    showToast('success', 'LLM 配置已保存')
  } catch (e) {
    showToast('error', `保存失败：${e.message}`)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page-header">
    <h1>LLM 配置</h1>
    <p>配置大语言模型 API 参数与对话行为</p>
  </div>

  <div class="page-body">
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">模型设置</div>
        <div class="action-bar">
          <button class="btn btn-secondary" :disabled="testing" @click="testConnection">
            <span :class="{ spin: testing }">⟳</span> {{ testing ? '测试中' : '测试连接' }}
          </button>
          <button class="btn btn-primary" :disabled="saving" @click="saveConfig">
            <span>✓</span> {{ saving ? '保存中' : '保存配置' }}
          </button>
        </div>
      </div>

      <div class="form-grid">
        <div class="form-group">
          <label>提供商</label>
          <select v-model="form.provider" @change="onProviderChange">
            <option v-for="opt in providerOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </div>
        <div class="form-group">
          <label>API 地址</label>
          <input v-model="form.api_url" type="text" placeholder="https://api.example.com/v1">
        </div>
        <div class="form-group">
          <label>API Key</label>
          <input v-model="form.api_key" type="password" placeholder="sk-..."></div>
        <div class="form-group">
          <label>模型名称</label>
          <input v-model="form.model" type="text" placeholder="gpt-4o-mini">
        </div>
        <div class="form-group">
          <label>最大 Token: {{ form.max_tokens }}</label>
          <input v-model.number="form.max_tokens" type="range" min="256" max="8192" step="256">
        </div>
        <div class="form-group">
          <label>温度 (Temperature): {{ form.temperature }}</label>
          <input v-model.number="form.temperature" type="range" min="0" max="2" step="0.1">
        </div>
        <div class="form-group">
          <label>最大历史轮数</label>
          <input v-model.number="form.max_history" type="number" min="0" max="100">
        </div>
        <div class="form-group full-width">
          <label>系统提示词 (System Prompt)</label>
          <textarea v-model="form.system_prompt" placeholder="定义 Bot 的人格、回复风格和知识边界..."></textarea>
        </div>
      </div>

      <transition name="toast">
        <div v-if="toast.show" class="toast" :class="toast.type">
          {{ toast.message }}
        </div>
      </transition>
    </div>

    <div class="card" style="margin-top: 22px;">
      <div class="card-header">
        <div class="card-title">人格管理</div>
        <div class="action-bar">
          <button class="btn btn-secondary" @click="addPersona">+ 添加人格</button>
        </div>
      </div>
      <div class="hint-text">
        聊天中通过 <code>/persona 名称</code> 切换会话人格，<code>/persona 列表</code> 查看全部，
        <code>/persona 重置</code> 恢复默认。未设置人格覆盖时使用下方「系统提示词」。
      </div>
      <div v-for="(p, idx) in form.personas" :key="idx" class="persona-block">
        <div class="persona-row">
          <input v-model="p.name" placeholder="人格名（如：猫娘）" class="persona-name">
          <input v-model="p.description" placeholder="简述" class="persona-desc">
          <button
            class="btn btn-secondary btn-sm"
            :disabled="!p.name"
            @click="toggleDefaultPersona(p.name)"
          >
            {{ form.default_persona === p.name ? '✓ 默认' : '设为默认' }}
          </button>
          <button class="btn btn-danger btn-sm" @click="removePersona(idx)">删除</button>
        </div>
        <textarea
          v-model="p.system_prompt"
          placeholder="该人格的 system prompt，例如：你是一只可爱的猫娘，喜欢用喵结尾..."
        ></textarea>
      </div>
      <div v-if="!form.personas.length" class="hint-text" style="margin-top: 12px;">
        暂无人格，点击「添加人格」开始配置。
      </div>
    </div>

    <div class="card" style="margin-top: 22px;">
      <div class="card-header">
        <div class="card-title">MCP 服务器</div>
        <div class="action-bar">
          <button class="btn btn-secondary" @click="addMcpServer">+ 添加服务器</button>
        </div>
      </div>
      <div class="hint-text">
        连接外部 MCP 服务器，将其工具注册为 <code>mcp_服务器名_工具名</code> 供 LLM 调用。
        需同时开启「工具调用」（enable_tools）。<strong>修改后需重启 Bot 生效。</strong>
      </div>
      <div v-for="(s, idx) in form.mcp_servers" :key="idx" class="persona-block">
        <div class="persona-row">
          <input v-model="s.name" placeholder="服务器名（如 filesystem）" class="mcp-name">
          <input v-model="s.command" placeholder="stdio 命令（如 npx / uvx / python）" class="mcp-cmd">
          <button class="btn btn-danger btn-sm" @click="removeMcpServer(idx)">删除</button>
        </div>
        <input v-model="s.args" placeholder="命令参数，逗号分隔（如 -y, @modelcontextprotocol/server-filesystem, /tmp）" class="mcp-args">
        <input v-model="s.url" placeholder="HTTP 模式地址（填写后忽略命令；如 http://localhost:8000/mcp）" class="mcp-args">
      </div>
      <div v-if="!form.mcp_servers.length" class="hint-text" style="margin-top: 12px;">
        暂未配置 MCP 服务器。
      </div>
    </div>

    <div class="card" style="margin-top: 22px;">
      <div class="card-header">
        <div class="card-title">常见配置参考</div>
      </div>
      <div class="hint-text">
        <strong>OpenAI:</strong> API 地址 https://api.openai.com/v1，模型 gpt-4o-mini<br>
        <strong>DeepSeek:</strong> API 地址 https://api.deepseek.com/v1，模型 deepseek-chat<br>
        <strong>Ollama:</strong> API 地址 http://localhost:11434/v1，模型 llama3.1
      </div>
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
.persona-block {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid var(--border-color, rgba(255, 255, 255, 0.08));
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.02);
}
.persona-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.persona-name { flex: 1; }
.persona-desc { flex: 2; }
.mcp-name { flex: 1; }
.mcp-cmd { flex: 2; }
.mcp-args {
  width: 100%;
  margin-bottom: 8px;
  box-sizing: border-box;
}
.mcp-args:last-child { margin-bottom: 0; }
.persona-block textarea {
  width: 100%;
  min-height: 72px;
  resize: vertical;
  box-sizing: border-box;
}
.persona-block input,
.persona-block textarea {
  padding: 8px 12px;
  border: 1px solid var(--border-color, rgba(255, 255, 255, 0.08));
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-color, #e6e6e6);
  font-size: 13px;
  line-height: 1.5;
}
.persona-block input:focus,
.persona-block textarea:focus {
  outline: none;
  border-color: var(--primary-color, #6f8ffc);
}
</style>