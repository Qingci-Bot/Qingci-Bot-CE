<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<script setup>
import { ref, onMounted } from 'vue';
import { useAppStore } from '../../stores/app';
import { useToast } from '../../composables/useToast';

const store = useAppStore();
const { showToast } = useToast();

const auditLogs = ref([]);
const auditLoading = ref(false);

onMounted(loadAuditLogs);

async function loadAuditLogs() {
  auditLoading.value = true;
  try {
    const data = await store.apiFetch('/api/audit/logs?limit=100');
    auditLogs.value = data.logs || [];
  } catch (e) {
    auditLogs.value = [];
    showToast('error', `审计日志加载失败：${e.message}`);
  } finally {
    auditLoading.value = false;
  }
}
</script>

<template>
  <div class="card fade-in">
    <div class="card-header">
      <div class="card-title">审计日志</div>
      <button class="btn btn-secondary btn-sm" :disabled="auditLoading" @click="loadAuditLogs">
        <span style="display: inline-block" :class="{ spin: auditLoading }">↻</span> 刷新
      </button>
    </div>
    <div v-if="auditLogs.length === 0" class="empty-state">
      <div class="icon">✦</div>
      <div>暂无审计日志</div>
    </div>
    <div v-else style="max-height: 420px; overflow-y: auto">
      <table class="table">
        <thead>
          <tr>
            <th class="audit-time">时间</th>
            <th class="audit-action">动作</th>
            <th class="audit-detail">详情</th>
            <th class="audit-ip">来源 IP</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in auditLogs" :key="log.id">
            <td class="audit-time">{{ log.created_at }}</td>
            <td>
              <span class="tag tag-blue">{{ log.action }}</span>
            </td>
            <td class="audit-detail">{{ log.detail || '-' }}</td>
            <td class="audit-ip">{{ log.client_ip || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* 审计日志列样式（收敛内联样式） */
.audit-time {
  width: 170px;
}
.audit-action {
  width: 170px;
}
.audit-ip {
  width: 120px;
}
td.audit-time {
  font-family: var(--font-mono);
  color: var(--text-muted);
  white-space: nowrap;
}
td.audit-ip {
  font-family: var(--font-mono);
}
td.audit-detail {
  word-break: break-all;
}
.spin {
  display: inline-block;
  animation: audit-spin 1s linear infinite;
}
@keyframes audit-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
