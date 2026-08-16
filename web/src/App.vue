<script setup>
import { onMounted, onUnmounted } from 'vue'
import { RouterView, RouterLink, useRoute } from 'vue-router'
import { useAppStore } from './stores/app'
import { useToast } from './composables/useToast'

const route = useRoute()
const store = useAppStore()
const { toast, showToast } = useToast()
let pollTimer = null
let pollDelay = 3000
let disposed = false

const navItems = [
  { path: '/', name: '仪表盘', icon: '◈' },
  { path: '/config', name: 'LLM 配置', icon: '✦' },
  { path: '/lab', name: '对话调试', icon: '✎' },
  { path: '/groups', name: '群配置', icon: '▣' },
  { path: '/plugins', name: '插件管理', icon: '◇' },
  { path: '/logs', name: '消息日志', icon: '✉' },
  { path: '/settings', name: '系统设置', icon: '⚙' },
  { path: '/about', name: '关于', icon: '♢' },
]

function isActive(path) {
  return route.path === path
}

// 心跳时间（epoch 秒）→ 相对时间文本
function fmtHeartbeat(ts) {
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

// setTimeout 链式调度：成功维持 3000ms；失败间隔翻倍（上限 30000ms），成功即重置
async function pollStatus() {
  if (disposed) return
  if (document.visibilityState === 'visible') {
    const ok = await store.fetchStatus()
    pollDelay = ok ? 3000 : Math.min(pollDelay * 2, 30000)
  }
  if (!disposed) {
    pollTimer = setTimeout(pollStatus, pollDelay)
  }
}

onMounted(() => {
  store.fetchConfig()
  store.fetchInstances()
  pollStatus()
})

onUnmounted(() => {
  disposed = true
  if (pollTimer) clearTimeout(pollTimer)
})

// ---- 实例操作 ----
function promptCreateInstance() {
  const name = window.prompt('新实例名称（字母/数字/-/_，用于目录名）')
  if (!name || !name.trim()) return
  showToast('info', '正在创建实例...')
  store.createInstance({ name: name.trim() })
    .then(() => showToast('success', `实例「${name.trim()}」已创建`))
    .then(() => store.fetchInstances())
    .catch((e) => showToast('error', e.message || '创建失败'))
}

function onSwitch(inst) {
  if (inst.running) return
  if (window.confirm(`切换到实例「${inst.name}」？应用将重启以加载该实例。`)) {
    store.switchInstance(inst.name)
  }
}

function onDelete(inst) {
  if (inst.running) return
  if (window.confirm(`删除实例「${inst.name}」及其 config/data/plugins 全部数据？此操作不可恢复。`)) {
    store.deleteInstance(inst.name)
      .then(() => showToast('success', `实例「${inst.name}」已删除`))
      .catch((e) => showToast('error', e.message || '删除失败'))
  }
}

function onRename(inst) {
  const newName = window.prompt(`将实例「${inst.name}」重命名为（字母/数字/-/_）`, inst.name)
  if (!newName || !newName.trim() || newName.trim() === inst.name) return
  if (inst.running) {
    // 重命名运行中实例会触发应用重启到新名称，连接随之断开（fire-and-forget）
    showToast('info', `正在重命名并重启到「${newName.trim()}」...`)
    store.renameInstance(inst.name, newName.trim()).catch(() => {})
    return
  }
  store.renameInstance(inst.name, newName.trim())
    .then(() => showToast('success', `已重命名为「${newName.trim()}」`))
    .catch((e) => showToast('error', e.message || '重命名失败'))
}
</script>

<template>
  <div id="app-root">
    <!-- 登录页：无侧边栏全屏展示 -->
    <RouterView v-if="route.path === '/login'" />

    <template v-else>
    <aside class="sidebar">
      <div class="sidebar-logo">
        <div class="title">Qingci-Bot CE</div>
        <span class="subtitle">QQ Bot Framework</span>
      </div>
      <div class="instance-section">
        <div class="instance-head">
          <span class="instance-title">实例</span>
          <button class="instance-add" title="新建实例" @click="promptCreateInstance">＋</button>
        </div>
        <div class="instance-list" v-if="store.instances.length">
          <div
            v-for="inst in store.instances"
            :key="inst.name"
            class="instance-item"
            :class="{ active: inst.running }"
            :title="inst.running ? '当前实例' : '点击切换到此实例'"
            @click="onSwitch(inst)"
          >
            <span class="instance-dot" :class="inst.running ? 'green' : 'gray'"></span>
            <span class="instance-name">{{ inst.name }}</span>
            <button
              class="instance-act"
              title="重命名实例"
              @click.stop="onRename(inst)"
            >✎</button>
            <button
              class="instance-del"
              title="删除实例"
              :disabled="inst.running"
              @click.stop="onDelete(inst)"
            >✕</button>
          </div>
        </div>
        <div class="instance-empty" v-else>
          <p class="instance-tip">还没有实例，创建第一个开始使用</p>
          <button class="instance-create-first" @click="promptCreateInstance">＋ 新建实例</button>
        </div>
      </div>
      <nav class="sidebar-nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          :class="{ active: isActive(item.path) }"
        >
          <span class="icon">{{ item.icon }}</span>
          <span>{{ item.name }}</span>
        </RouterLink>
      </nav>
      <div style="margin-top: auto; padding: 16px 12px 0; border-top: 1px solid var(--border-color);">
        <div class="status-badge" style="width: 100%; justify-content: center;">
          <span class="status-dot" :class="{
            green: store.botRunning && store.botConnected,
            yellow: store.botRunning && !store.botConnected,
            gray: !store.botRunning
          }"></span>
          <span>{{ store.statusText }}</span>
        </div>
        <div v-if="store.platforms.length" class="platform-status">
          <div
            v-for="p in store.platforms"
            :key="p.name"
            class="platform-row"
            :title="`${p.display_name}${p.self_id ? ' · self_id ' + p.self_id : ''}${p.last_heartbeat ? ' · 心跳 ' + fmtHeartbeat(p.last_heartbeat) : ''}`"
          >
            <span class="status-dot" :class="p.connected ? 'green' : (store.botRunning ? 'yellow' : 'gray')"></span>
            <span class="platform-name">{{ p.display_name }}</span>
            <span class="platform-state">{{ p.connected ? '在线' : '离线' }}</span>
          </div>
        </div>
      </div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div class="left">
          <span class="breadcrumb">Qingci-Bot CE</span>
          <span style="color: var(--text-muted);">/</span>
          <span class="breadcrumb" style="color: var(--text-primary);">
            {{ navItems.find(n => n.path === route.path)?.name || '仪表盘' }}
          </span>
        </div>
        <div class="action-bar">
          <button
            v-if="!store.botRunning"
            class="btn btn-success btn-sm"
            :disabled="store.loading"
            @click="store.startBot"
          >
            <span>▶</span> 启动 Bot
          </button>
          <button
            v-else
            class="btn btn-danger btn-sm"
            :disabled="store.loading"
            @click="store.stopBot"
          >
            <span>■</span> 停止 Bot
          </button>
          <button
            class="btn btn-secondary btn-sm"
            :disabled="store.loading || !store.botRunning"
            @click="store.restartBot"
          >
            <span style="display: inline-block" :class="{ spin: store.loading }">↻</span> 重启
          </button>
        </div>
      </header>

      <div class="route-container">
        <RouterView v-slot="{ Component }">
          <component :is="Component" :key="route.fullPath" />
        </RouterView>
      </div>
    </main>
    </template>

    <transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type">{{ toast.message }}</div>
    </transition>
  </div>
</template>

<style scoped>
.route-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.instance-section {
  padding: 12px 12px 4px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 8px;
}
.instance-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.instance-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 1px;
}
.instance-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-hover);
  color: var(--accent);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
  transition: all 0.2s ease;
}
.instance-add:hover {
  background: var(--accent-bg);
  border-color: rgba(251, 191, 36, 0.3);
  transform: translateY(-1px);
}
.instance-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.instance-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: var(--radius-xs);
  border: 1px solid transparent;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-secondary);
  transition: all 0.2s ease;
}
.instance-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.instance-item.active {
  background: var(--accent-bg);
  color: var(--accent);
  border-color: rgba(251, 191, 36, 0.2);
  box-shadow: 0 0 12px var(--accent-glow);
}
.instance-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 0 6px currentColor;
}
.instance-dot.green {
  background: var(--success);
  color: var(--success);
}
.instance-dot.gray {
  background: var(--text-muted);
  color: var(--text-muted);
}
.instance-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.instance-item.active .instance-name {
  font-weight: 600;
}
.instance-act,
.instance-del {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
  opacity: 1;
  transition: all 0.2s ease;
}
.instance-act:hover:not(:disabled) {
  background: var(--blue-bg);
  color: var(--blue);
}
.instance-del {
  color: var(--text-muted);
}
.instance-del:hover:not(:disabled) {
  background: var(--danger-bg);
  color: var(--danger);
}
.instance-act:disabled,
.instance-del:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.instance-empty {
  font-size: 12px;
  color: var(--text-muted);
  padding: 4px 8px 10px;
}
.instance-tip {
  margin: 0 0 8px;
}
.instance-create-first {
  width: 100%;
  padding: 7px 0;
  border: 1px dashed rgba(251, 191, 36, 0.35);
  border-radius: var(--radius-xs);
  background: var(--bg-hover);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}
.instance-create-first:hover {
  background: var(--accent-bg);
  border-color: rgba(251, 191, 36, 0.5);
  transform: translateY(-1px);
}

.toast {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9999;
  padding: 10px 18px;
  border-radius: 8px;
  font-size: 14px;
  color: #fff;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
}
.toast.success {
  background: #10b981;
}
.toast.error {
  background: #ef4444;
}
.toast.info {
  background: #3b82f6;
}
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-8px);
}
</style>