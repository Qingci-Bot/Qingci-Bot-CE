<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<script setup>
import { ref } from 'vue';
import { authHeaders } from '../../api/request';
import { useAppStore } from '../../stores/app';
import { useToast } from '../../composables/useToast';

const store = useAppStore();
const { showToast } = useToast();

const backupLoading = ref(false);
const backupResult = ref(null);
const downloading = ref(false);
const restoreFile = ref(null);
const restoreLoading = ref(false);
const restoreResult = ref('');
const restoreFileName = ref('');
const exporting = ref(false);

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

async function backupDb() {
  backupLoading.value = true;
  backupResult.value = null;
  try {
    const data = await store.apiFetch('/api/backup/db', { method: 'POST' });
    backupResult.value = data;
    showToast('success', `数据库备份成功：${data.filename}`);
  } catch (e) {
    showToast('error', `备份失败：${e.message}`);
  } finally {
    backupLoading.value = false;
  }
}

async function downloadBackup() {
  const filename = backupResult.value?.filename;
  if (!filename) {
    showToast('error', '请先执行一次备份再下载');
    return;
  }
  downloading.value = true;
  try {
    // blob 下载场景不走 request（其返回 JSON），仅复用鉴权头注入
    const res = await fetch(`/api/backup/db/download?filename=${encodeURIComponent(filename)}`, {
      headers: authHeaders(),
    });
    if (!res.ok) {
      const text = await res.text();
      let msg = text || `HTTP ${res.status}`;
      try {
        msg = JSON.parse(text).detail || msg;
      } catch (e) {
        // 非 JSON 响应体，直接使用原文本
      }
      if (res.status === 401 && window.location.hash !== '#/login') {
        window.location.hash = '#/login';
      }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    showToast('success', '数据库备份已开始下载');
  } catch (e) {
    showToast('error', `下载失败：${e.message}`);
  } finally {
    downloading.value = false;
  }
}

function pickRestoreFile() {
  restoreFile.value?.click();
}

function onRestorePick() {
  const file = restoreFile.value?.files?.[0];
  restoreFileName.value = file ? file.name : '';
}

async function restoreDb() {
  const file = restoreFile.value?.files?.[0];
  if (!file) {
    showToast('error', '请先选择 .db 备份文件');
    return;
  }
  if (!file.name.toLowerCase().endsWith('.db')) {
    showToast('error', '只支持 .db 数据库备份文件');
    return;
  }
  if (!window.confirm('恢复会用所选备份覆盖当前数据库，操作前系统会自动备份当前库。确认继续？')) {
    return;
  }
  restoreLoading.value = true;
  restoreResult.value = '';
  try {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/backup/restore', {
      method: 'POST',
      headers: authHeaders(),
      body: fd,
    });
    if (!res.ok) {
      const text = await res.text();
      let msg = text || `HTTP ${res.status}`;
      try {
        msg = JSON.parse(text).detail || msg;
      } catch (e) {
        // 非 JSON 响应体，直接使用原文本
      }
      if (res.status === 401 && window.location.hash !== '#/login') {
        window.location.hash = '#/login';
      }
      throw new Error(msg);
    }
    const data = await res.json();
    restoreResult.value = `恢复成功。原库已自动备份为 ${data.backup_name}，请刷新页面查看恢复后的数据。`;
    showToast('success', '数据库恢复成功');
  } catch (e) {
    restoreResult.value = `恢复失败：${e.message}`;
    showToast('error', `恢复失败：${e.message}`);
  } finally {
    restoreLoading.value = false;
    if (restoreFile.value) restoreFile.value.value = '';
    restoreFileName.value = '';
  }
}

async function exportCsv() {
  exporting.value = true;
  try {
    // blob 下载场景不走 request（其返回 JSON），仅复用鉴权头注入
    const res = await fetch('/api/log/messages/export', {
      headers: authHeaders(),
    });
    if (!res.ok) {
      const text = await res.text();
      let msg = text || `HTTP ${res.status}`;
      try {
        msg = JSON.parse(text).detail || msg;
      } catch (e) {
        // 非 JSON 响应体，直接使用原文本
      }
      // 401 时跳转登录页，与 apiFetch 的鉴权失败行为保持一致
      if (res.status === 401 && window.location.hash !== '#/login') {
        window.location.hash = '#/login';
      }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'messages.csv';
    a.click();
    URL.revokeObjectURL(url);
    showToast('success', '消息 CSV 已开始下载');
  } catch (e) {
    showToast('error', `导出失败：${e.message}`);
  } finally {
    exporting.value = false;
  }
}
</script>

<template>
  <div class="card fade-in">
    <div class="card-header">
      <div class="card-title">数据管理</div>
      <div class="action-bar">
        <button class="btn btn-secondary" :disabled="backupLoading" @click="backupDb">
          <span style="display: inline-block" :class="{ spin: backupLoading }">◍</span>
          {{ backupLoading ? '备份中' : '立即备份' }}
        </button>
        <button
          class="btn btn-secondary"
          :disabled="!backupResult || downloading"
          @click="downloadBackup"
        >
          <span style="display: inline-block" :class="{ spin: downloading }">⇩</span>
          {{ downloading ? '下载中' : '下载备份' }}
        </button>
        <button class="btn btn-secondary" :disabled="exporting" @click="exportCsv">
          <span style="display: inline-block" :class="{ spin: exporting }">⇩</span>
          {{ exporting ? '导出中' : '导出消息 CSV' }}
        </button>
      </div>
    </div>
    <div v-if="backupResult" class="status-bar success">
      备份完成：{{ backupResult.filename }}（{{ formatSize(backupResult.size) }}），保存在服务端
      data/backups/ 目录
    </div>
    <div class="restore-row">
      <input
        ref="restoreFile"
        type="file"
        accept=".db"
        class="file-input-hidden"
        :disabled="restoreLoading"
        @change="onRestorePick"
      />
      <button class="btn btn-secondary" :disabled="restoreLoading" @click="pickRestoreFile">
        选择备份文件…
      </button>
      <span class="file-name" :class="{ 'file-name-empty': !restoreFileName }">
        {{ restoreFileName || '未选择文件' }}
      </span>
      <button
        class="btn btn-primary"
        :disabled="!restoreFileName || restoreLoading"
        @click="restoreDb"
      >
        {{ restoreLoading ? '恢复中…' : '从备份恢复' }}
      </button>
    </div>
    <div v-if="restoreResult" class="status-bar" style="margin-top: 8px">{{ restoreResult }}</div>
    <div class="hint-text restore-hint">
      <p>备份在服务端进行（数据库保持可用，不停机），服务端保留最近 10 份，可随时下载到本地。</p>
      <p>恢复时上传 <code>.db</code> 备份：会先自动备份当前库，再替换主库（恢复后请刷新页面）。</p>
      <p>CSV 导出包含全部消息记录，兼容 Excel 直接打开。</p>
    </div>
  </div>
</template>

<style scoped>
.spin {
  display: inline-block;
  animation: data-spin 1s linear infinite;
}
@keyframes data-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* 恢复行：原生 file input 在各平台渲染差异大（“选择文件 未选择任何文件”），
   改为统一的按钮 + 文件名展示，并与上方操作按钮对齐 */
.restore-row {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--border);
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.file-input-hidden {
  display: none;
}
.file-name {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
}
.file-name-empty {
  font-family: var(--font-sans);
  color: var(--text-muted);
}
.restore-hint p {
  margin: 0 0 4px;
  line-height: 1.7;
}
.restore-hint p:last-child {
  margin-bottom: 0;
}
.restore-hint code {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--bg-hover);
  font-family: var(--font-mono);
  font-size: 11px;
}
</style>
