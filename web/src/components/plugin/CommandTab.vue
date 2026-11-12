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

const commands = ref([]);
const commandLoading = ref('');

// 组件随 Tab 切换挂载（父级 v-if），每次进入命令管理页自动拉取
onMounted(fetchCommands);

async function fetchCommands() {
  try {
    commands.value = await store.apiFetch('/api/command/conflicts');
  } catch (e) {
    // 失败置 null，模板显示错误态而非"暂无命令"
    commands.value = null;
    showToast('error', `获取命令列表失败：${e.message}`);
  }
}

async function toggleCommand(cmd) {
  commandLoading.value = `${cmd.plugin}/${cmd.command}`;
  try {
    const body = { disabled: !cmd.disabled };
    const res = await store.apiFetch(
      `/api/command/${encodeURIComponent(cmd.plugin)}/${encodeURIComponent(cmd.command)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
    cmd.disabled = res.disabled;
    showToast('success', `命令 ${cmd.plugin}/${cmd.command} 已${cmd.disabled ? '禁用' : '启用'}`);
  } catch (e) {
    showToast('error', `操作失败：${e.message}`);
  } finally {
    commandLoading.value = '';
  }
}

async function updatePriority(cmd, newPriority) {
  const val = parseInt(newPriority);
  if (isNaN(val) || val < 0 || val > 100) return;
  commandLoading.value = `${cmd.plugin}/${cmd.command}`;
  try {
    const res = await store.apiFetch(
      `/api/command/${encodeURIComponent(cmd.plugin)}/${encodeURIComponent(cmd.command)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ priority: val }),
      },
    );
    cmd.priority = res.priority;
    showToast('success', `优先级已更新为 ${val}`);
  } catch (e) {
    showToast('error', `更新失败：${e.message}`);
  } finally {
    commandLoading.value = '';
  }
}

// 权限等级中文映射（含组合标签，如 "(SUPERUSER & PRIVATE)"）
const permZh = {
  SUPERUSER: '超级管理员',
  ADMIN: '管理员',
  EVERYONE: '所有人',
  MEMBER: '群成员',
  PRIVATE: '私聊',
  GROUP: '群聊',
  CUSTOM: '自定义',
};
const permissionLabel = (p) => {
  if (!p) return '-';
  let s = String(p);
  for (const [k, v] of Object.entries(permZh)) s = s.replaceAll(k, v);
  return s;
};
</script>

<template>
  <div class="card fade-in">
    <div class="card-header">
      <div class="card-title">已注册命令</div>
      <span class="text-muted">共 {{ commands?.length ?? 0 }} 条</span>
    </div>
    <div v-if="commands === null" class="empty-state">
      <div class="icon">⚠</div>
      <div>命令列表加载失败</div>
      <button class="btn btn-secondary btn-sm" style="margin-top: 12px" @click="fetchCommands">
        <span>↻</span> 重试
      </button>
    </div>
    <div v-else-if="commands.length === 0" class="empty-state">
      <div class="icon">◇</div>
      <div>暂无命令</div>
    </div>
    <div v-else class="command-table-wrap">
      <table class="command-table">
        <thead>
          <tr>
            <th>命令</th>
            <th>插件</th>
            <th>事件</th>
            <th>权限</th>
            <th>优先级</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(cmd, idx) in commands"
            :key="idx"
            :class="{ 'conflict-row': cmd.has_conflict }"
          >
            <td>
              <span class="cmd-name">/{{ cmd.command }}</span>
              <span v-if="cmd.has_conflict" class="conflict-badge" title="存在同名命令冲突">⚠</span>
            </td>
            <td>
              <span class="tag tag-accent">{{ cmd.plugin }}</span>
            </td>
            <td>
              <span class="tag tag-purple">{{ cmd.event_type }}</span>
            </td>
            <td>
              <span class="tag tag-perm">{{ permissionLabel(cmd.permission) }}</span>
            </td>
            <td>
              <input
                type="number"
                class="priority-input"
                :value="cmd.priority"
                min="0"
                max="100"
                @change="updatePriority(cmd, $event.target.value)"
              />
            </td>
            <td>
              <span :class="['status-tag', cmd.disabled ? 'status-off' : 'status-on']">
                {{ cmd.disabled ? '已禁用' : '启用' }}
              </span>
            </td>
            <td>
              <button
                class="btn btn-sm"
                :class="cmd.disabled ? 'btn-success' : 'btn-warning'"
                :disabled="commandLoading === `${cmd.plugin}/${cmd.command}`"
                @click="toggleCommand(cmd)"
              >
                {{ cmd.disabled ? '启用' : '禁用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* 命令管理表格 */
.command-table-wrap {
  overflow-x: auto;
}
.command-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.command-table th,
.command-table td {
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}
.command-table th {
  color: var(--text-muted);
  font-weight: 500;
  font-size: 12px;
  white-space: nowrap;
}
.command-table td {
  color: var(--text-secondary);
}
.command-table .conflict-row {
  background: rgba(248, 113, 113, 0.08);
}
.command-table .conflict-row td:first-child {
  border-left: 3px solid var(--danger);
}
.cmd-name {
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  font-weight: 500;
  color: var(--text-primary);
}
.conflict-badge {
  margin-left: 6px;
  font-size: 14px;
  color: var(--danger);
  cursor: help;
}
.priority-input {
  width: 52px;
  padding: 3px 6px;
  border-radius: 4px;
  border: 1px solid var(--border-color);
  background: rgba(0, 0, 0, 0.2);
  color: var(--text-primary);
  font-size: 12px;
  text-align: center;
  font-family: inherit;
}
.priority-input:focus {
  outline: none;
  border-color: var(--accent);
}
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}
.status-on {
  background: rgba(52, 211, 153, 0.15);
  color: var(--success);
}
.status-off {
  background: rgba(148, 163, 184, 0.15);
  color: var(--text-muted);
}
</style>
