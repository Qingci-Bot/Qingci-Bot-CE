<script setup>
import { ref, onMounted } from 'vue'
import { useAppStore } from '../stores/app'
import { useToast } from '../composables/useToast'

const store = useAppStore()
const { showToast } = useToast()
const defaults = ref({ enabled: true, trigger_mode: null })
const groups = ref([])
const loading = ref(false)
const savingId = ref(null)
const newGroupId = ref('')

const triggerOptions = [
  { value: '', label: '跟随全局' },
  { value: 'always', label: '所有消息都回复' },
  { value: 'at', label: '被 @ 时回复' },
  { value: 'keyword', label: '关键词触发' },
]

onMounted(() => {
  loadGroups()
})

async function loadGroups() {
  loading.value = true
  try {
    const data = await store.apiFetch('/api/group/list')
    if (data.defaults) defaults.value = data.defaults
    groups.value = (data.groups || []).map(g => ({
      group_id: g.group_id,
      enabled: g.enabled !== false,
      triggerValue: g.trigger_mode || '',
    }))
  } catch (e) {
    showToast('error', `加载群列表失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

async function updateGroup(group, payload) {
  savingId.value = group.group_id
  try {
    const data = await store.apiFetch(`/api/group/${group.group_id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    group.enabled = data.enabled !== false
    group.triggerValue = data.trigger_mode || ''
    showToast('success', `群 ${group.group_id} 配置已更新`)
  } catch (e) {
    showToast('error', `保存失败：${e.message}`)
    await loadGroups()
  } finally {
    savingId.value = null
  }
}

function onEnabledChange(group, event) {
  // 以 DOM 实际勾选状态为准（:checked 绑定不会自动同步 group.enabled）
  const newValue = event.target.checked
  group.enabled = newValue
  updateGroup(group, { enabled: newValue })
}

function onTriggerChange(group) {
  // "跟随全局" 对应显式传 null，清除群级覆盖
  const payload = group.triggerValue === ''
    ? { trigger_mode: null }
    : { trigger_mode: group.triggerValue }
  updateGroup(group, payload)
}

async function addGroup() {
  const id = Number(newGroupId.value.trim())
  if (!id || !Number.isInteger(id)) {
    showToast('error', '请输入有效的群号（纯数字）')
    return
  }
  if (groups.value.some(g => g.group_id === id)) {
    showToast('info', `群 ${id} 已在列表中`)
    newGroupId.value = ''
    return
  }
  const group = { group_id: id, enabled: true, triggerValue: '' }
  await updateGroup(group, { enabled: true, trigger_mode: null })
  newGroupId.value = ''
  await loadGroups()
}
</script>

<template>
  <div class="page-header">
    <h1>群配置</h1>
    <p>按群粒度覆盖启用状态与触发模式（未单独配置的群跟随全局设置）</p>
  </div>

  <div class="page-body">
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">群列表</div>
        <div class="action-bar">
          <div class="input-group">
            <div class="form-group">
              <input v-model="newGroupId" type="number" placeholder="输入群号添加" @keyup.enter="addGroup">
            </div>
            <button class="btn btn-secondary btn-sm" :disabled="savingId !== null" @click="addGroup">
              <span>＋</span> 添加群
            </button>
          </div>
          <button class="btn btn-secondary btn-sm" :disabled="loading" @click="loadGroups">
            <span style="display: inline-block" :class="{ spin: loading }">↻</span> 刷新
          </button>
        </div>
      </div>

      <div class="hint-text">
        <strong>全局默认：</strong>{{ defaults.enabled === false ? '停用' : '启用' }} ·
        触发模式 {{ defaults.trigger_mode || '（未设置）' }}。
        触发模式选择"跟随全局"表示清除该群的独立设置。
      </div>

      <div v-if="groups.length === 0" class="empty-state">
        <div class="icon">▣</div>
        <div>暂无已配置的群</div>
        <div style="font-size: 12px; margin-top: 6px;">可在上方输入群号手动添加</div>
      </div>

      <table v-else class="table">
        <thead>
          <tr>
            <th class="col-group-id">群号</th>
            <th class="col-enabled">启用</th>
            <th>触发模式</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="group in groups" :key="group.group_id">
            <td style="font-family: var(--font-mono); font-weight: 600;">{{ group.group_id }}</td>
            <td>
              <label class="switch">
                <input
                  type="checkbox"
                  :checked="group.enabled"
                  :disabled="savingId === group.group_id"
                  @change="onEnabledChange(group, $event)"
                >
                <span class="slider"></span>
              </label>
            </td>
            <td>
              <select
                v-model="group.triggerValue"
                class="inline-select"
                :disabled="savingId === group.group_id"
                @change="onTriggerChange(group)"
              >
                <option v-for="opt in triggerOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.col-group-id { width: 180px; }
.col-enabled { width: 120px; }

.inline-select {
  padding: 7px 12px;
  min-width: 180px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  transition: all 0.2s ease;
  outline: none;
}

.inline-select:hover { border-color: var(--border-active); }

.inline-select:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-bg);
}

.inline-select:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
