<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { RouterView, RouterLink, useRoute } from 'vue-router';
import { useAppStore } from './stores/app';
import { useToast } from './composables/useToast';

const route = useRoute();
const store = useAppStore();
const { toasts, showToast } = useToast();
let pollTimer = null;
let pollDelay = 3000;
let disposed = false;

// 消费 store.error（如 401 鉴权失败文案）：变化时以顶部 toast 展示；
// 请求成功后 store 内部会清空 error，再次失败时重新提示
watch(
  () => store.error,
  (msg) => {
    if (msg) showToast('error', msg);
  },
  { immediate: true },
);

// 左下角平台状态：仅显示当前激活实例的主平台
const activePlatforms = computed(() => {
  const main = store.currentInstance?.platform || 'onebot';
  return store.platforms.filter((p) => p.name === main);
});

// 侧边栏导航项：运行日志项跟随 `log.run_log_enabled` 配置显隐，
// 关闭运行日志采集时隐藏入口（与设置页开关一致，避免出现"无数据入口"）
const navItems = computed(() => {
  const runLogEnabled = (store.config.log && store.config.log.run_log_enabled) !== false;
  const items = [
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
  if (runLogEnabled) {
    // 按路径定位插入，避免依赖下标（导航项重排后下标会插错位置）
    const settingsIdx = items.findIndex((it) => it.path === '/settings');
    items.splice(settingsIdx === -1 ? items.length : settingsIdx, 0, {
      path: '/runlog',
      name: '运行日志',
      icon: '⌘',
    });
  }
  return items;
});

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

// 平台状态三态：在线 / 未配置（用户还没填凭据）/ 离线（配了但连不上）
function isUnconfigured(p) {
  return !p.connected && typeof p.reason === 'string' && p.reason.startsWith('未配置');
}
function platformStateLabel(p) {
  if (p.connected) return '在线';
  return isUnconfigured(p) ? '未配置' : '离线';
}
function platformStateClass(p) {
  if (p.connected) return 'green';
  return isUnconfigured(p) ? 'gray' : store.botRunning ? 'yellow' : 'gray';
}
function platformTitle(p) {
  const parts = [p.display_name];
  if (p.self_id) parts.push(`self_id ${p.self_id}`);
  if (p.last_heartbeat) parts.push(`心跳 ${fmtHeartbeat(p.last_heartbeat)}`);
  if (!p.connected && p.reason) parts.push(p.reason);
  return parts.join(' · ');
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
              :title="platformTitle(p)"
            >
              <span class="status-dot" :class="platformStateClass(p)" />
              <span class="platform-name">{{ p.display_name }}</span>
              <span class="platform-state">{{ platformStateLabel(p) }}</span>
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
              <span class="spin-btn-icon" :class="{ spin: store.loading }">↻</span> 重启 Bot
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

    <transition-group name="toast" tag="div" class="toast-stack">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type">{{ t.message }}</div>
    </transition-group>
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

/* toast 队列：纵向堆叠展示 */
.toast-stack {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9999;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}
.toast {
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
