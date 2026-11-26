<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<!--
  自定义下拉选择，替代原生 <select>。

  为什么不用原生的：原生下拉在暗色主题下由操作系统绘制，Windows 上是白底、
  macOS 上会跟随系统外观但与页面配色不一致，Linux 各发行版差异更大；展开面板
  的字号/行高也不受页面控制。这里用按钮 + 绝对定位面板，样式与页面完全一致。

  可访问性：role="listbox"/"option"、键盘 ↑↓ 移动、Enter 选择、Esc 关闭、
  点击外部关闭；打开时焦点留在触发器上（不抢焦点，避免滚动跳动）。
-->
<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: '' },
  // 支持 ['a','b'] 或 [{ value, label }]
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '请选择' },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(['update:modelValue', 'change']);

const rootRef = ref(null);
const open = ref(false);
const hoverIndex = ref(-1);

const normalized = computed(() =>
  props.options.map((opt) =>
    typeof opt === 'object' && opt !== null
      ? { value: opt.value, label: opt.label ?? String(opt.value) }
      : { value: opt, label: String(opt) },
  ),
);

const selectedLabel = computed(() => {
  const hit = normalized.value.find((o) => o.value === props.modelValue);
  return hit ? hit.label : '';
});

function toggle() {
  if (props.disabled) return;
  open.value = !open.value;
  if (open.value) {
    // 打开时把高亮停在当前值上，键盘操作才有明确起点
    const idx = normalized.value.findIndex((o) => o.value === props.modelValue);
    hoverIndex.value = idx >= 0 ? idx : 0;
    nextTick(() => rootRef.value?.querySelector('.as-panel')?.focus?.());
  }
}

function pick(opt) {
  if (opt.value !== props.modelValue) {
    emit('update:modelValue', opt.value);
    emit('change', opt.value);
  }
  open.value = false;
}

function move(step) {
  if (!normalized.value.length) return;
  const next = (hoverIndex.value + step + normalized.value.length) % normalized.value.length;
  hoverIndex.value = next;
}

function onTriggerKeydown(e) {
  if (props.disabled) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!open.value) {
      toggle();
      return;
    }
    move(e.key === 'ArrowDown' ? 1 : -1);
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    if (!open.value) {
      toggle();
    } else if (hoverIndex.value >= 0) {
      pick(normalized.value[hoverIndex.value]);
    }
  } else if (e.key === 'Escape') {
    open.value = false;
  }
}

function onDocClick(e) {
  if (open.value && rootRef.value && !rootRef.value.contains(e.target)) open.value = false;
}

// 值被外部改动（如切换预设回填）时，高亮跟随
watch(
  () => props.modelValue,
  () => {
    const idx = normalized.value.findIndex((o) => o.value === props.modelValue);
    if (idx >= 0) hoverIndex.value = idx;
  },
);

onMounted(() => document.addEventListener('mousedown', onDocClick));
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick));
</script>

<template>
  <div ref="rootRef" class="app-select" :class="{ 'is-open': open, 'is-disabled': disabled }">
    <button
      type="button"
      class="as-trigger"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
      @keydown="onTriggerKeydown"
    >
      <span class="as-value" :class="{ 'is-placeholder': !selectedLabel }">
        {{ selectedLabel || placeholder }}
      </span>
      <span class="as-caret" aria-hidden="true">▾</span>
    </button>

    <transition name="as-pop">
      <ul v-if="open" class="as-panel" role="listbox" tabindex="-1">
        <li
          v-for="(opt, i) in normalized"
          :key="String(opt.value)"
          class="as-option"
          :class="{ 'is-selected': opt.value === modelValue, 'is-hover': i === hoverIndex }"
          role="option"
          :aria-selected="opt.value === modelValue"
          @mouseenter="hoverIndex = i"
          @click="pick(opt)"
        >
          <span class="as-option-label">{{ opt.label }}</span>
          <span v-if="opt.value === modelValue" class="as-option-check" aria-hidden="true">✓</span>
        </li>
        <li v-if="!normalized.length" class="as-empty">无可选项</li>
      </ul>
    </transition>
  </div>
</template>

<style scoped>
.app-select {
  position: relative;
  min-width: 0;
}

.as-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
  padding: 11px 14px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 14px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

.as-trigger:hover:not(:disabled) {
  border-color: var(--border-active);
}

.as-trigger:focus-visible,
.is-open .as-trigger {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.12);
}

.as-value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.as-value.is-placeholder {
  color: var(--text-muted);
}

.as-caret {
  flex-shrink: 0;
  font-size: 12px;
  line-height: 1;
  color: var(--text-secondary);
  transition: transform 0.15s ease;
}

.is-open .as-caret {
  transform: rotate(180deg);
}

.as-panel {
  position: absolute;
  z-index: 60;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  max-height: 260px;
  overflow-y: auto;
  margin: 0;
  padding: 6px;
  list-style: none;
  background: #131a2b;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5);
}

.as-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;
}

.as-option.is-hover {
  background: var(--bg-hover);
}

.as-option.is-selected {
  color: var(--accent);
  font-weight: 600;
}

.as-option-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.as-option-check {
  flex-shrink: 0;
  font-size: 12px;
}

.as-empty {
  padding: 10px;
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
}

.is-disabled .as-trigger {
  opacity: 0.55;
  cursor: not-allowed;
}

.as-pop-enter-active,
.as-pop-leave-active {
  transition:
    opacity 0.12s ease,
    transform 0.12s ease;
}

.as-pop-enter-from,
.as-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
