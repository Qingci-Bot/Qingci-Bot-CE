<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<script setup>
import { ref, computed } from 'vue';
import Drawer from '../Drawer.vue';
import { useAppStore } from '../../stores/app';
import { useToast } from '../../composables/useToast';

const store = useAppStore();
const { showToast } = useToast();

const open = ref(false);
const plugin = ref(null);
const schema = ref(null);
const values = ref({});
const saving = ref(false);
const loading = ref(false);

async function openFor(target) {
  plugin.value = target;
  open.value = true;
  loading.value = true;
  try {
    const data = await store.apiFetch(`/api/plugin/${encodeURIComponent(target.name)}/config`);
    schema.value = data.schema;
    // 以 schema 默认值补全，保证必填字段有初值
    const merged = {};
    const props = data.schema?.properties || {};
    for (const key of Object.keys(props)) {
      merged[key] = props[key].default !== undefined ? props[key].default : '';
    }
    Object.assign(merged, data.values || {});
    values.value = merged;
  } catch (e) {
    showToast('error', `获取配置失败：${e.message}`);
    close();
  } finally {
    loading.value = false;
  }
}

function close() {
  open.value = false;
  plugin.value = null;
  schema.value = null;
  values.value = {};
}

defineExpose({ open: openFor });

const fields = computed(() => {
  if (!schema.value?.properties) return [];
  const required = schema.value.required || [];
  return Object.entries(schema.value.properties).map(([key, prop]) => ({
    key,
    title: prop.title || key,
    type: prop.type || 'string',
    description: prop.description || '',
    required: required.includes(key),
    default: prop.default,
  }));
});

function inputType(field) {
  if (field.type === 'boolean') return 'checkbox';
  if (field.type === 'integer' || field.type === 'number') return 'number';
  return 'text';
}

async function save() {
  saving.value = true;
  try {
    const res = await store.apiFetch(
      `/api/plugin/${encodeURIComponent(plugin.value.name)}/config`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values: values.value }),
      },
    );
    values.value = res;
    showToast('success', `插件 ${plugin.value.name} 配置已保存`);
  } catch (e) {
    showToast('error', `保存失败：${e.message}`);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <Drawer
    :open="open"
    icon="⚙"
    :title="`${plugin?.name} - 配置`"
    body-class="config-drawer-body"
    @close="close"
  >
    <div v-if="loading" class="empty-state">加载配置中...</div>
    <div v-else-if="!schema" class="empty-state">
      <div class="icon">◇</div>
      <div>该插件未定义配置项（无 Config 内嵌类）</div>
    </div>
    <div v-else class="config-form">
      <div v-if="fields.length === 0" class="empty-state">该插件无配置字段</div>
      <div v-for="field in fields" :key="field.key" class="config-field">
        <label class="config-label">
          <span class="config-title">
            {{ field.title }}
            <span v-if="field.required" class="required-mark" title="必填">*</span>
          </span>
          <span v-if="field.description" class="config-desc">{{ field.description }}</span>
        </label>
        <label v-if="field.type === 'boolean'" class="switch">
          <input v-model="values[field.key]" type="checkbox" />
          <span class="slider round" />
        </label>
        <input
          v-else
          v-model="values[field.key]"
          :type="inputType(field)"
          class="config-input"
          :step="field.type === 'number' ? '0.1' : undefined"
        />
      </div>
      <div class="config-actions">
        <button class="btn btn-secondary" :disabled="saving" @click="close">取消</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">
          <span :class="{ spin: saving }">✓</span> 保存配置
        </button>
      </div>
    </div>
  </Drawer>
</template>

<style scoped>
/* 插件配置表单 */
.config-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.config-field {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border-color);
}
.config-label {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.config-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}
.required-mark {
  color: var(--danger);
}
.config-desc {
  font-size: 12px;
  color: var(--text-muted);
}
.config-input {
  width: 220px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: rgba(0, 0, 0, 0.2);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  flex-shrink: 0;
}
.config-input:focus {
  outline: none;
  border-color: var(--accent);
}
.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}
.spin {
  display: inline-block;
  animation: config-spin 1s linear infinite;
}
@keyframes config-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
