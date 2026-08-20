<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useWebSocket } from '../composables/useWebSocket';

// 运行日志：实时查看框架运行日志（ERROR/WARN/INFO），支持按级别过滤与清屏
const MAX_ENTRIES = 500;

const wsConnected = ref(false);
const entries = ref([]);
const levelFilter = ref('ALL');
const autoScroll = ref(true);
const containerRef = ref(null);

const LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const { connect, disconnect } = useWebSocket('/api/ws/runlog', {
  onOpen: () => {
    wsConnected.value = true;
  },
  onClose: () => {
    wsConnected.value = false;
  },
  onMessage: (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (e) {
      console.warn('运行日志解析失败:', e);
      return;
    }
    if (!data || data.type === 'ping') return;
    if (data.type === 'snapshot') {
      // 新连接：用服务端环形缓冲快照初始化历史
      entries.value = (data.entries || []).slice(-MAX_ENTRIES);
      return;
    }
    if (data.entry) {
      entries.value.push(data.entry);
      if (entries.value.length > MAX_ENTRIES) entries.value.shift();
    }
  },
});

// 连接状态用于显示：wsConnected 由 onOpen/onClose 维护，此处无需额外逻辑
function getLevelClass(level) {
  return String(level || '').toUpperCase();
}

function filterEntries() {
  if (levelFilter.value === 'ALL') return entries.value;
  return entries.value.filter((e) => String(e.level || '').toUpperCase() === levelFilter.value);
}

function clearScreen() {
  entries.value = [];
}

function toggleAutoScroll() {
  autoScroll.value = !autoScroll.value;
}

// 自动滚动到底部：新日志到达或切换级别时若开启自动滚动则跟随
function scrollToBottom() {
  if (!autoScroll.value || !containerRef.value) return;
  nextTick(() => {
    if (containerRef.value) containerRef.value.scrollTop = containerRef.value.scrollHeight;
  });
}
watch(filterEntries, scrollToBottom, { deep: true });
onMounted(scrollToBottom);

onMounted(() => {
  connect();
});

onUnmounted(() => {
  disconnect();
});
</script>

<template>
  <div class="page-header">
    <h1>运行日志</h1>
    <p>实时查看框架运行日志（运行日志采集开关位于系统设置 → 日志）</p>
  </div>

  <div class="page-body">
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">
          运行日志
          <span class="tag tag-status" :class="wsConnected ? 'tag-perm' : 'tag-danger'">
            {{ wsConnected ? '实时推送中' : '推送断开' }}
          </span>
        </div>
        <div class="runlog-actions">
          <label class="level-filter">
            级别
            <select v-model="levelFilter">
              <option v-for="lv in LEVELS" :key="lv" :value="lv">{{ lv }}</option>
            </select>
          </label>
          <button class="btn btn-secondary btn-sm" @click="toggleAutoScroll">
            {{ autoScroll ? '自动滚动：开' : '自动滚动：关' }}
          </button>
          <button class="btn btn-secondary btn-sm" @click="clearScreen">清屏</button>
        </div>
      </div>

      <div ref="containerRef" class="runlog-container">
        <div v-if="filterEntries().length === 0" class="empty-state">
          <div class="icon">⌘</div>
          <div>暂无运行日志，Bot 启动后自动产生</div>
        </div>
        <div v-for="(log, i) in filterEntries()" :key="i" class="log-line">
          <span class="ln-time">{{ log.time }}</span>
          <span class="ln-level" :class="getLevelClass(log.level)">{{ log.level }}</span>
          <span class="ln-name">{{ log.name }}</span>
          <span class="ln-msg">{{ log.message }}</span>
          <span v-if="log.event_id" class="ln-event">{{ log.event_id }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tag-status {
  margin-left: 10px;
}
.runlog-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.level-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-secondary);
}
.level-filter select {
  padding: 4px 8px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.6);
  color: var(--text-primary);
  font-size: 13px;
}
.runlog-container {
  min-height: 360px;
  max-height: calc(100vh - 320px);
  overflow-y: auto;
  padding: 8px 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  border-top: 1px solid var(--border-color);
}
.log-line {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 1px 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-line:hover {
  background: rgba(255, 255, 255, 0.03);
}
.ln-time {
  color: var(--text-muted);
  flex-shrink: 0;
}
.ln-level {
  flex-shrink: 0;
  width: 60px;
  font-weight: 600;
}
.ln-level.INFO {
  color: var(--blue, #38bdf8);
}
.ln-level.DEBUG {
  color: var(--text-muted);
}
.ln-level.WARNING {
  color: #fbbf24;
}
.ln-level.ERROR,
.ln-level.CRITICAL {
  color: #f87171;
}
.ln-name {
  color: var(--text-secondary);
  flex-shrink: 0;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ln-msg {
  color: var(--text-primary);
  flex: 1;
  min-width: 0;
}
.ln-event {
  color: #d4a72c;
  background: rgba(251, 191, 36, 0.12);
  border-radius: 4px;
  padding: 0 4px;
  font-size: 11px;
  flex-shrink: 0;
}
</style>