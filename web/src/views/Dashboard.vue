<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const messageCount = ref(0)
const usage = ref(null)
const usageLoading = ref(false)

onMounted(async () => {
  try {
    const data = await store.fetchMessageCount()
    messageCount.value = data?.count || 0
  } catch (e) {
    messageCount.value = 0
  }
  loadUsage()
})

async function loadUsage() {
  usageLoading.value = true
  try {
    usage.value = await store.apiFetch('/api/log/usage?days=30')
  } catch (e) {
    usage.value = null
  } finally {
    usageLoading.value = false
  }
}

const usageDaily = computed(() => usage.value?.daily || [])
const usageSummary = computed(() => usage.value?.summary || { calls: 0, total_tokens: 0, prompt_tokens: 0, completion_tokens: 0 })
const maxTokens = computed(() => Math.max(1, ...usageDaily.value.map(d => d.total_tokens || 0)))

function formatNum(n) {
  return (n || 0).toLocaleString('zh-CN')
}

function barHeight(d) {
  return `${Math.max(2, Math.round(((d.total_tokens || 0) / maxTokens.value) * 120))}px`
}

function barTitle(d) {
  return `${d.date}\n总 token：${formatNum(d.total_tokens)}（prompt ${formatNum(d.prompt_tokens)} / completion ${formatNum(d.completion_tokens)}）\n调用次数：${formatNum(d.calls)}`
}

function showLabel(index) {
  return index % 5 === 0 || index === usageDaily.value.length - 1
}

const triggerDesc = {
  always: '所有消息都回复',
  at: '被 @ 时回复',
  keyword: '触发关键词时回复',
}
</script>

<template>
  <div class="page-header">
    <h1>仪表盘</h1>
    <p>Bot 运行状态概览与快捷控制</p>
  </div>

  <div class="page-body">
    <div class="grid grid-4 fade-in">
      <div class="card stat-card">
        <span class="stat-icon">◉</span>
        <div class="stat-label">Bot 状态</div>
        <div class="stat-value" :style="{ color: store.statusColor }">{{ store.statusText }}</div>
        <div class="stat-desc">{{ store.botConnected ? 'LLBot 已连接' : '等待协议端连接' }}</div>
      </div>
      <div class="card stat-card">
        <span class="stat-icon" style="color: var(--blue)">◇</span>
        <div class="stat-label">已加载插件</div>
        <div class="stat-value" style="color: var(--blue)">{{ store.plugins.length }}</div>
        <div class="stat-desc">内置 + 外部插件总数</div>
      </div>
      <div class="card stat-card">
        <span class="stat-icon" style="color: var(--purple)">✉</span>
        <div class="stat-label">消息记录</div>
        <div class="stat-value" style="color: var(--purple)">{{ messageCount }}</div>
        <div class="stat-desc">数据库中保存的消息数</div>
      </div>
      <div class="card stat-card">
        <span class="stat-icon" style="color: var(--accent)">✦</span>
        <div class="stat-label">LLM 模型</div>
        <div class="stat-value" style="margin-top: 8px; color: var(--accent)">
          {{ store.config.llm?.model || '-' }}
        </div>
        <div class="stat-desc">{{ store.config.llm?.provider || '未配置' }}</div>
      </div>
    </div>

    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">近 30 天 LLM 用量</div>
        <button class="btn btn-secondary btn-sm" :disabled="usageLoading" @click="loadUsage">
          <span style="display: inline-block" :class="{ spin: usageLoading }">↻</span> 刷新
        </button>
      </div>
      <div v-if="usage" class="grid grid-2" style="margin-bottom: 18px;">
        <div class="stat-card">
          <div class="stat-label">总调用次数</div>
          <div class="stat-value" style="color: var(--blue);">{{ formatNum(usageSummary.calls) }}</div>
          <div class="stat-desc">近 {{ usage.days || 30 }} 天 LLM 调用总数</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">总 Token 用量</div>
          <div class="stat-value" style="color: var(--accent);">{{ formatNum(usageSummary.total_tokens) }}</div>
          <div class="stat-desc">prompt {{ formatNum(usageSummary.prompt_tokens) }} · completion {{ formatNum(usageSummary.completion_tokens) }}</div>
        </div>
      </div>
      <div v-if="usage && usageDaily.length === 0" class="empty-state">
        <div class="icon">▤</div>
        <div>近 30 天暂无用量数据</div>
      </div>
      <div v-else-if="usage" class="usage-chart">
        <div
          v-for="(d, i) in usageDaily"
          :key="d.date"
          class="usage-bar-wrap"
          :title="barTitle(d)"
        >
          <div class="usage-bar" :style="{ height: barHeight(d) }"></div>
          <div class="usage-date">{{ showLabel(i) ? d.date.slice(5) : '' }}</div>
        </div>
      </div>
      <div v-else class="empty-state">
        <div class="icon">▤</div>
        <div>用量数据加载失败</div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-header">
          <div class="card-title">快捷操作</div>
        </div>
        <div class="action-bar" style="margin-bottom: 18px;">
          <button v-if="!store.botRunning" class="btn btn-success btn-sm" :disabled="store.loading" @click="store.startBot">
            <span>▶</span> 启动 Bot
          </button>
          <button v-else class="btn btn-danger btn-sm" :disabled="store.loading" @click="store.stopBot">
            <span>■</span> 停止 Bot
          </button>
          <button class="btn btn-secondary btn-sm" :disabled="store.loading || !store.botRunning" @click="store.restartBot">
            <span style="display: inline-block" :class="{ spin: store.loading }">↻</span> 重启 Bot
          </button>
        </div>
        <div class="hint-text">
          <strong>OneBot 反向 WS 地址：</strong>ws://{{ store.config.onebot?.host || '127.0.0.1' }}:{{ store.config.onebot?.port || 3001 }}<br>
          在 LLBot 中添加该反向 WebSocket 连接，即可让 QQ 消息流入本框架。
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div class="card-title">插件列表</div>
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
                <span class="tag tag-accent" style="margin-left: 8px;">{{ plugin.version }}</span>
              </div>
              <div class="desc">{{ plugin.description || '无描述' }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">当前配置快照</div>
      </div>
      <div class="grid grid-3">
        <div>
          <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 6px;">触发模式</div>
          <div style="font-weight: 600;">{{ triggerDesc[store.config.bot?.trigger_mode] || store.config.bot?.trigger_mode || '-' }}</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 6px;">API 地址</div>
          <div style="font-weight: 600; font-size: 13px;">{{ store.config.llm?.api_url || '-' }}</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 6px;">系统提示词</div>
          <div style="font-weight: 600; font-size: 13px; color: var(--text-secondary);">
            {{ store.config.llm?.system_prompt?.slice(0, 40) || '-' }}{{ (store.config.llm?.system_prompt?.length || 0) > 40 ? '...' : '' }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.usage-chart {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  padding: 14px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  overflow-x: auto;
}

.usage-bar-wrap {
  flex: 1;
  min-width: 10px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.usage-bar {
  width: 100%;
  max-width: 18px;
  border-radius: 3px 3px 0 0;
  background: linear-gradient(180deg, var(--blue), rgba(56, 189, 248, 0.35));
  transition: all 0.2s ease;
}

.usage-bar-wrap:hover .usage-bar {
  background: linear-gradient(180deg, var(--accent), rgba(251, 191, 36, 0.4));
  box-shadow: 0 0 10px var(--accent-glow);
}

.usage-date {
  font-size: 10px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  white-space: nowrap;
  height: 12px;
}
</style>