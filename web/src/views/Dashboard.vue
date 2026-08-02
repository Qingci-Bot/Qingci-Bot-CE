<script setup>
import { onMounted, ref } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const messageCount = ref(0)

onMounted(async () => {
  try {
    const data = await store.fetchMessageCount()
    messageCount.value = data?.count || 0
  } catch (e) {
    messageCount.value = 0
  }
})

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
        <div class="stat-value" style="font-size: 18px; margin-top: 8px; color: var(--accent)">
          {{ store.config.llm?.model || '-' }}
        </div>
        <div class="stat-desc">{{ store.config.llm?.provider || '未配置' }}</div>
      </div>
    </div>

    <div class="grid grid-2" style="margin-top: 22px;">
      <div class="card">
        <div class="card-header">
          <div class="card-title">快捷操作</div>
        </div>
        <div class="action-bar" style="margin-bottom: 18px;">
          <button v-if="!store.botRunning" class="btn btn-success btn-lg" :disabled="store.loading" @click="store.startBot">
            <span>▶</span> 启动 Bot
          </button>
          <button v-else class="btn btn-danger btn-lg" :disabled="store.loading" @click="store.stopBot">
            <span>■</span> 停止 Bot
          </button>
          <button class="btn btn-secondary btn-lg" :disabled="store.loading || !store.botRunning" @click="store.restartBot">
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

    <div class="card" style="margin-top: 22px;">
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
.grid-4 { grid-template-columns: repeat(4, 1fr); }
@media (max-width: 1100px) { .grid-4 { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 640px) { .grid-4 { grid-template-columns: 1fr; } }
</style>