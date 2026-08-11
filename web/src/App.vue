<script setup>
import { onMounted, onUnmounted } from 'vue'
import { RouterView, RouterLink, useRoute } from 'vue-router'
import { useAppStore } from './stores/app'

const route = useRoute()
const store = useAppStore()
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
  pollStatus()
})

onUnmounted(() => {
  disposed = true
  if (pollTimer) clearTimeout(pollTimer)
})
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
  </div>
</template>

<style scoped>
.route-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>