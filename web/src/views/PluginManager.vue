<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAppStore } from '../stores/app'
import { useToast } from '../composables/useToast'

const store = useAppStore()
const { toast, showToast } = useToast()
const modulePath = ref('')
const loading = ref('')
const activeCategory = ref('all')
const expandedMetrics = ref('')

onMounted(() => {
  store.fetchStatus()
})

const categories = computed(() => {
  const cats = new Set(store.plugins.map(p => p.category || '未分类'))
  return ['all', ...Array.from(cats).sort()]
})

const filteredPlugins = computed(() => {
  if (activeCategory.value === 'all') return store.plugins
  return store.plugins.filter(p => (p.category || '未分类') === activeCategory.value)
})

const statusLabel = (s) => ({ loading: '加载中', loaded: '已加载', disabled: '已禁用', error: '错误', unloading: '卸载中' }[s] || s)
const statusClass = (s) => ({ loaded: 'green', loading: 'yellow', disabled: 'gray', error: 'red', unloading: 'yellow' }[s] || 'gray')

async function reload(name) {
  loading.value = name
  try {
    await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}/reload`, { method: 'POST' })
    await store.fetchStatus()
    showToast('success', `插件 ${name} 已重载`)
  } catch (e) {
    showToast('error', `重载失败：${e.message}`)
  } finally {
    loading.value = ''
  }
}

async function loadExternal() {
  if (!modulePath.value.trim()) return
  loading.value = '__load__'
  try {
    await store.apiFetch('/api/plugin/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module_path: modulePath.value.trim() }),
    })
    modulePath.value = ''
    await store.fetchStatus()
    showToast('success', '插件已加载')
  } catch (e) {
    showToast('error', `加载失败：${e.message}`)
  } finally {
    loading.value = ''
  }
}

async function unload(name) {
  loading.value = name
  try {
    await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}`, { method: 'DELETE' })
    await store.fetchStatus()
    showToast('success', `插件 ${name} 已卸载`)
  } catch (e) {
    showToast('error', `卸载失败：${e.message}`)
  } finally {
    loading.value = ''
  }
}

async function toggleEnabled(plugin) {
  loading.value = plugin.name
  const action = plugin.enabled ? 'disable' : 'enable'
  try {
    await store.apiFetch(`/api/plugin/${encodeURIComponent(plugin.name)}/${action}`, { method: 'POST' })
    await store.fetchStatus()
    showToast('success', `插件 ${plugin.name} 已${plugin.enabled ? '禁用' : '启用'}`)
  } catch (e) {
    showToast('error', `${action === 'disable' ? '禁用' : '启用'}失败：${e.message}`)
  } finally {
    loading.value = ''
  }
}

async function toggleMetrics(name) {
  if (expandedMetrics.value === name) {
    expandedMetrics.value = ''
    return
  }
  expandedMetrics.value = name
  try {
    const data = await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}/metrics`)
    const plugin = store.plugins.find(p => p.name === name)
    if (plugin) plugin._metrics = data
  } catch (e) {
    showToast('error', `获取指标失败：${e.message}`)
    expandedMetrics.value = ''
  }
}
</script>

<template>
  <div class="page-header">
    <h1>插件管理</h1>
    <p>查看、重载、加载、卸载、禁用和启用 Bot 插件</p>
  </div>

  <div class="page-body">
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">加载外部插件</div>
      </div>
      <div class="input-group">
        <div class="form-group" style="flex: 1;">
          <label>Python 模块路径</label>
          <input v-model="modulePath" type="text" placeholder="例如：plugins.my_plugin">
        </div>
        <button class="btn btn-primary" :disabled="loading === '__load__'" @click="loadExternal">
          <span>＋</span> 加载
        </button>
      </div>
    </div>

    <div class="card fade-in" style="margin-top: 22px;">
      <div class="card-header">
        <div class="card-title">已加载插件</div>
        <div class="category-tabs">
          <button
            v-for="cat in categories" :key="cat"
            :class="['tab-btn', { active: activeCategory === cat }]"
            @click="activeCategory = cat"
          >
            {{ cat === 'all' ? '全部' : cat }}
          </button>
        </div>
      </div>
      <div v-if="filteredPlugins.length === 0" class="empty-state">
        <div class="icon">◇</div>
        <div>{{ activeCategory === 'all' ? '暂无插件' : '该分类下暂无插件' }}</div>
      </div>
      <div v-else>
        <div v-for="plugin in filteredPlugins" :key="plugin.name" class="plugin-card">
          <div class="plugin-info">
            <div class="name">
              {{ plugin.name }}
              <span class="tag tag-accent">{{ plugin.version }}</span>
              <span v-if="plugin.category" class="tag tag-purple" style="margin-left: 6px;">{{ plugin.category }}</span>
              <span v-if="plugin.author" class="tag tag-blue" style="margin-left: 6px;">{{ plugin.author }}</span>
            </div>
            <div class="desc">{{ plugin.description || '无描述' }}</div>
            <div class="plugin-meta-row">
              <span class="status-badge" :class="statusClass(plugin.status)">
                <span class="status-dot" :class="statusClass(plugin.status)"></span>
                {{ statusLabel(plugin.status) }}
              </span>
              <button class="btn-metrics" @click="toggleMetrics(plugin.name)" :title="expandedMetrics === plugin.name ? '收起指标' : '查看指标'">
                <span :class="{ spin: expandedMetrics === plugin.name && !plugin._metrics }">⏱</span> 指标
              </button>
            </div>
            <!-- 指标面板 -->
            <div v-if="expandedMetrics === plugin.name && plugin._metrics" class="metrics-panel">
              <div v-if="plugin._metrics.length === 0" class="empty-state" style="padding: 12px; font-size: 12px;">
                暂无指标数据
              </div>
              <table v-else class="metrics-table">
                <thead>
                  <tr>
                    <th>Handler</th>
                    <th>事件</th>
                    <th>优先级</th>
                    <th>调用</th>
                    <th>平均</th>
                    <th>错误</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="m in plugin._metrics" :key="m.handler">
                    <td>{{ m.handler }}</td>
                    <td><span class="tag tag-accent" style="font-size: 10px;">{{ m.event_type }}</span></td>
                    <td>{{ m.priority }}</td>
                    <td>{{ m.call_count }}</td>
                    <td>{{ m.avg_time_ms }}ms</td>
                    <td :class="{ 'text-danger': m.error_count > 0 }">{{ m.error_count }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="action-bar">
            <label class="switch" :title="plugin.enabled ? '点击禁用' : '点击启用'">
              <input type="checkbox" :checked="plugin.enabled"
                     @change="toggleEnabled(plugin)" :disabled="loading === plugin.name">
              <span class="slider round"></span>
            </label>
            <button class="btn btn-secondary btn-sm" :disabled="loading === plugin.name" @click="reload(plugin.name)">
              <span :class="{ spin: loading === plugin.name }">↻</span> 重载
            </button>
            <button class="btn btn-danger btn-sm" :disabled="loading === plugin.name" @click="unload(plugin.name)">
              卸载
            </button>
          </div>
        </div>
      </div>
      <transition name="toast">
        <div v-if="toast.show" class="toast" :class="toast.type" style="margin-top: 16px;">
          {{ toast.message }}
        </div>
      </transition>
    </div>
  </div>
</template>

<style scoped>
/* 分类标签 */
.category-tabs {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.tab-btn {
  padding: 4px 12px;
  border-radius: 20px;
  border: 1px solid var(--border-color);
  background: rgba(255,255,255,0.03);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}
.tab-btn:hover { border-color: var(--border-active); color: var(--text-primary); }
.tab-btn.active { background: var(--accent-bg); color: var(--accent); border-color: rgba(251, 191, 36, 0.3); }

/* 插件元信息行 */
.plugin-meta-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}
.btn-metrics {
  padding: 2px 10px;
  border-radius: 12px;
  border: 1px solid var(--border-color);
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
}
.btn-metrics:hover { color: var(--text-primary); border-color: var(--border-active); }

/* 指标面板 */
.metrics-panel {
  margin-top: 10px;
  padding: 10px;
  background: rgba(0,0,0,0.2);
  border-radius: var(--radius-xs);
  border: 1px solid var(--border-color);
}
.metrics-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.metrics-table th, .metrics-table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}
.metrics-table th {
  color: var(--text-muted);
  font-weight: 500;
  font-size: 11px;
}
.metrics-table td { color: var(--text-secondary); }
.text-danger { color: var(--danger) !important; }

/* 状态标记 */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
  background: rgba(255,255,255,0.05);
  border: 1px solid var(--border-color);
}
.status-badge.green { border-color: rgba(52, 211, 153, 0.3); color: var(--success); }
.status-badge.yellow { border-color: rgba(251, 191, 36, 0.3); color: var(--warning); }
.status-badge.gray { border-color: rgba(148, 163, 184, 0.2); color: var(--text-muted); }
.status-badge.red { border-color: rgba(248, 113, 113, 0.3); color: var(--danger); }

/* 开关按钮 */
.switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
  flex-shrink: 0;
}
.switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(148, 163, 184, 0.2);
  border: 1px solid var(--border-color);
  transition: 0.25s;
}
.slider.round { border-radius: 24px; }
.slider::before {
  content: '';
  position: absolute;
  height: 16px; width: 16px;
  left: 3px; bottom: 3px;
  background: var(--text-secondary);
  transition: 0.25s;
  border-radius: 50%;
}
.switch input:checked + .slider {
  background: var(--accent-bg);
  border-color: rgba(251, 191, 36, 0.4);
}
.switch input:checked + .slider::before {
  transform: translateX(20px);
  background: var(--accent);
}
.switch input:disabled + .slider {
  opacity: 0.5;
  cursor: not-allowed;
}

.toast-enter-active, .toast-leave-active {
  transition: all 0.3s ease;
}
.toast-enter-from, .toast-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>