<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<!--
  滑杆，替代原生 <input type="range">。

  原生 range 的轨道与滑块由系统绘制：Windows/Chrome 上是亮色轨道 + 圆形白
  滑块，在暗色卡片里非常突兀，且各平台高度不一致会让表单行高跳动。这里自绘
  轨道与滑块，并保留键盘操作（←→ 步进、Home/End 到端点）与 role="slider"。
-->
<script setup>
import { computed, onBeforeUnmount, ref } from 'vue';

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(['update:modelValue', 'change']);

const trackRef = ref(null);
const dragging = ref(false);

const pct = computed(() => {
  const span = props.max - props.min;
  if (span <= 0) return 0;
  return Math.min(100, Math.max(0, ((props.modelValue - props.min) / span) * 100));
});

function clampToStep(raw) {
  const stepped = Math.round((raw - props.min) / props.step) * props.step + props.min;
  const bounded = Math.min(props.max, Math.max(props.min, stepped));
  // 消掉浮点累加误差（0.1 步长时容易出现 0.30000000000000004）
  return Number(bounded.toFixed(6));
}

function valueFromEvent(e) {
  const el = trackRef.value;
  if (!el) return props.modelValue;
  const rect = el.getBoundingClientRect();
  const ratio = rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0;
  return clampToStep(props.min + Math.min(1, Math.max(0, ratio)) * (props.max - props.min));
}

function apply(value, final = false) {
  if (props.disabled || value === props.modelValue) return;
  emit('update:modelValue', value);
  if (final) emit('change', value);
}

function onPointerDown(e) {
  if (props.disabled) return;
  dragging.value = true;
  apply(valueFromEvent(e), false);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
}

function onPointerMove(e) {
  if (!dragging.value) return;
  apply(valueFromEvent(e), false);
}

function onPointerUp(e) {
  dragging.value = false;
  window.removeEventListener('pointermove', onPointerMove);
  window.removeEventListener('pointerup', onPointerUp);
  apply(valueFromEvent(e), true);
}

function onKeydown(e) {
  const keys = {
    ArrowLeft: -props.step,
    ArrowDown: -props.step,
    ArrowRight: props.step,
    ArrowUp: props.step,
  };
  if (e.key in keys) {
    e.preventDefault();
    apply(clampToStep(props.modelValue + keys[e.key]), true);
  } else if (e.key === 'Home') {
    e.preventDefault();
    apply(props.min, true);
  } else if (e.key === 'End') {
    e.preventDefault();
    apply(props.max, true);
  }
}

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onPointerMove);
  window.removeEventListener('pointerup', onPointerUp);
});
</script>

<template>
  <div
    ref="trackRef"
    class="app-slider"
    :class="{ 'is-disabled': disabled, 'is-dragging': dragging }"
    role="slider"
    tabindex="0"
    :aria-valuemin="min"
    :aria-valuemax="max"
    :aria-valuenow="modelValue"
    :aria-disabled="disabled"
    @pointerdown="onPointerDown"
    @keydown="onKeydown"
  >
    <div class="as-rail">
      <div class="as-fill" :style="{ width: pct + '%' }" />
    </div>
    <div class="as-thumb" :style="{ left: pct + '%' }" />
  </div>
</template>

<style scoped>
.app-slider {
  position: relative;
  height: 22px;
  display: flex;
  align-items: center;
  cursor: pointer;
  touch-action: none;
  outline: none;
}

.as-rail {
  width: 100%;
  height: 5px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.22);
  overflow: hidden;
}

.as-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(251, 191, 36, 0.6), var(--accent));
}

.as-thumb {
  position: absolute;
  top: 50%;
  width: 14px;
  height: 14px;
  margin-left: -7px;
  transform: translateY(-50%);
  border-radius: 50%;
  background: #f8fafc;
  border: 2px solid var(--accent);
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.5);
  transition: transform 0.1s ease;
}

.app-slider:focus-visible .as-thumb,
.app-slider.is-dragging .as-thumb {
  transform: translateY(-50%) scale(1.12);
}

.app-slider.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
