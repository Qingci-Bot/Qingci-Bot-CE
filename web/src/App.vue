<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { RouterView, RouterLink, useRoute } from 'vue-router';
import { useAppStore } from './stores/app';
import { useToast } from './composables/useToast';

const route = useRoute();
const store = useAppStore();
const { toast } = useToast();
let pollTimer = null;
let pollDelay = 3000;
let disposed = false;

// 左下角平台状态：仅显示当前激活实例的主平台
const activePlatforms = computed(() => {
  const main = store.currentInstance?.platform || 'onebot';
  return store.platforms.filter((p) => p.name === main);
});

const navItems = [
  { path: '/instances', name: '实例管理', icon: '⊞' },
  { path: '/', name: '仪表盘', icon: '◈' },
  { path: '/config', name: 'LLM 配置', icon: '✦' },
  { path: '/lab', name: '对话调试', icon: '✎' },
  { path: '/groups', name: '群组配置', icon: '▣' },
  { path: '/plugins', name: '插件管理', icon: '◇' },
  { path: '/logs', name: '消息日志', icon: '✉' },
  { path: '/settings', name: '系统设置', icon: '⚙' },
  { path: '/about', name: '关于', icon: '♢' },
];

function isActive(path) {
  return route.path === path;
}

// 心跳时间（epoch 秒）→ 相对时间文本
function fmtHeartbeat(ts) {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

// setTimeout 链式调度：成功维持 3000ms；失败间隔翻倍（上限 30000ms），成功即重置
async function pollStatus() {
  if (disposed) return;
  if (document.visibilityState === 'visible') {
    const ok = await store.fetchStatus();
    pollDelay = ok ? 3000 : Math.min(pollDelay * 2, 30000);
  }
  if (!disposed) {
    pollTimer = setTimeout(pollStatus, pollDelay);
  }
}

onMounted(() => {
  store.fetchConfig();
  store.fetchInstances();
  pollStatus();
});

onUnmounted(() => {
  disposed = true;
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<template>
  <div id="app-root">
    <!-- 登录页/首次向导：无侧边栏全屏展示 -->
    <RouterView v-if="route.path === '/login' || route.path === '/setup'" />

    <template v-else>
      <aside class="sidebar">
        <div class="sidebar-logo">
          <div class="title">Qingci-Bot CE</div>
          <span class="subtitle">Multi-Platform Bot Framework</span>
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
        <div class="sidebar-footer">
          <div class="status-badge sidebar-status">
            <span
              class="status-dot"
              :class="{
                green: store.botRunning && store.botConnected,
                yellow: store.botRunning && !store.botConnected,
                gray: !store.botRunning,
              }"
            />
            <span>{{ store.statusText }}</span>
          </div>
          <div v-if="activePlatforms.length" class="platform-status">
            <div
              v-for="p in activePlatforms"
              :key="p.name"
              class="platform-row"
              :title="`${p.display_name}${p.self_id ? ' · self_id ' + p.self_id : ''}${p.last_heartbeat ? ' · 心跳 ' + fmtHeartbeat(p.last_heartbeat) : ''}`"
            >
              <span
                class="status-dot"
                :class="p.connected ? 'green' : store.botRunning ? 'yellow' : 'gray'"
              />
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
            <span class="breadcrumb-sep">/</span>
            <span class="breadcrumb breadcrumb-current">
              {{ navItems.find((n) => n.path === route.path)?.name || '仪表盘' }}
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
              <span class="spin-btn-icon" :class="{ spin: store.loading }">↻</span> 重启
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

/* 侧边栏底部状态区 */
.sidebar-footer {
  margin-top: auto;
  padding: 16px 12px 12px;
  border-top: 1px solid var(--border-color);
}
.sidebar-status {
  width: 100%;
  justify-content: center;
}

/* 顶栏面包屑 */
.breadcrumb-sep {
  color: var(--text-muted);
}
.breadcrumb-current {
  color: var(--text-primary);
}

/* 按钮内旋转图标（行内图标保持 inline-block 以便 transform 生效） */
.spin-btn-icon {
  display: inline-block;
}

.btn-sm {
  padding: 4px 10px;
  font-size: 12px;
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
  transition:
    opacity 0.25s ease,
    transform 0.25s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-8px);
}
</style>
