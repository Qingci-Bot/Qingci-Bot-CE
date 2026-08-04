<script setup>
import { ref, onMounted } from 'vue'
import { useAppStore } from '../stores/app'
import { useToast } from '../composables/useToast'

const store = useAppStore()
const { toast, showToast } = useToast()
const modulePath = ref('')
const loading = ref('')

onMounted(() => {
  store.fetchStatus()
})

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
</script>

<template>
  <div class="page-header">
    <h1>插件管理</h1>
    <p>查看、重载、加载和卸载 Bot 插件</p>
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
      </div>
      <div v-if="store.plugins.length === 0" class="empty-state">
        <div class="icon">◇</div>
        <div>暂无插件</div>
      </div>
      <div v-else>
        <div v-for="plugin in store.plugins" :key="plugin.name" class="plugin-card">
          <div class="plugin-info">
            <div class="name">
              {{ plugin.name }}
              <span class="tag tag-accent">{{ plugin.version }}</span>
              <span v-if="plugin.author" class="tag tag-blue" style="margin-left: 6px;">{{ plugin.author }}</span>
            </div>
            <div class="desc">{{ plugin.description || '无描述' }}</div>
          </div>
          <div class="action-bar">
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
.toast-enter-active, .toast-leave-active {
  transition: all 0.3s ease;
}
.toast-enter-from, .toast-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>