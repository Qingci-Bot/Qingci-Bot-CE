<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<!--
  开关（布尔值），替代原生 <input type="checkbox"> 的「立刻生效」语义。

  原生 checkbox 的问题是外观由操作系统绘制：Windows 上是白底方框，与暗色
  页面严重冲突；且点击区域只有那个小方框。这里做成整块可点击的轨道式开关，
  并保留 role="switch" + aria-checked 供读屏与键盘（空格/回车）使用。
-->
<script setup>
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  // 小尺寸用于表格/紧凑行内
  size: { type: String, default: 'md' }, // md | sm
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
    class="app-switch"
    :class="[`is-${size}`, { 'is-on': modelValue, 'is-disabled': disabled }]"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="toggle"
  >
    <span class="as-knob" />
  </button>
</template>

<style scoped>
.app-switch {
  --w: 42px;
  --h: 24px;
  --pad: 3px;
  position: relative;
  flex-shrink: 0;
  width: var(--w);
  height: var(--h);
  padding: 0;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.8);
  cursor: pointer;
  transition:
    background 0.18s ease,
    border-color 0.18s ease;
}

.app-switch.is-sm {
  --w: 34px;
  --h: 20px;
  --pad: 2px;
}

.app-switch:hover:not(.is-disabled) {
  border-color: var(--border-active);
}

.app-switch:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.18);
}

.app-switch.is-on {
  background: var(--accent);
  border-color: var(--accent);
}

.as-knob {
  position: absolute;
  top: var(--pad);
  left: var(--pad);
  width: calc(var(--h) - var(--pad) * 2 - 2px);
  height: calc(var(--h) - var(--pad) * 2 - 2px);
  border-radius: 50%;
  background: #94a3b8;
  transition:
    transform 0.18s ease,
    background 0.18s ease;
}

.app-switch.is-on .as-knob {
  background: #1a1206;
  transform: translateX(calc(var(--w) - var(--h) + 2px));
}

.app-switch.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
