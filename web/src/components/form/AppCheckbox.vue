<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<!--
  勾选框，替代原生 <input type="checkbox"> 的「表单提交」语义。

  与 AppSwitch 的分工：开关用于「立刻生效的配置项」，勾选框用于一组并列选项
  或多选列表（如插件 JSON Schema 里的 boolean 字段）——一排开关会让人误以为
  每项都在独立生效。原生 checkbox 的方框由系统绘制，暗色下是刺眼的白底。
-->
<script setup>
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: '' },
});

const emit = defineEmits(['update:modelValue', 'change']);

function toggle() {
  if (props.disabled) return;
  const next = !props.modelValue;
  emit('update:modelValue', next);
  emit('change', next);
}
</script>

<template>
  <button
    type="button"
    class="app-checkbox"
    :class="{ 'is-checked': modelValue, 'is-disabled': disabled }"
    role="checkbox"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="toggle"
  >
    <span class="ac-box" aria-hidden="true">
      <svg v-if="modelValue" viewBox="0 0 16 16" class="ac-tick">
        <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2" />
      </svg>
    </span>
    <span v-if="label" class="ac-label">{{ label }}</span>
    <slot />
  </button>
</template>

<style scoped>
.app-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  background: none;
  border: none;
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}

.ac-box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.6);
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.app-checkbox:hover:not(.is-disabled) .ac-box {
  border-color: var(--border-active);
}

.app-checkbox:focus-visible {
  outline: none;
}

.app-checkbox:focus-visible .ac-box {
  box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.18);
}

.is-checked .ac-box {
  background: var(--accent);
  border-color: var(--accent);
}

.ac-tick {
  width: 12px;
  height: 12px;
  color: #1a1206;
}

.ac-label {
  line-height: 1.5;
}

.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
