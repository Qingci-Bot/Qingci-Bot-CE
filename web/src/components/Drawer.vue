<script setup>
/**
 * 通用右侧抽屉（Teleport 到 body）
 *
 * 用法：
 *   <Drawer :open="open" icon="📊" title="标题" @close="open = false">
 *     抽屉内容（slot）
 *   </Drawer>
 *
 * Props:
 *   - open: 是否显示
 *   - icon: 标题图标字符
 *   - title: 标题文本
 *   - bodyClass: 追加到 body 容器的额外 class（如滚动/内边距变体）
 */
defineProps({
  open: { type: Boolean, default: false },
  icon: { type: String, default: '◇' },
  title: { type: String, default: '' },
  bodyClass: { type: String, default: '' },
});
const emit = defineEmits(['close']);
</script>

<template>
  <Teleport to="body">
    <transition name="drawer">
      <div v-if="open" class="drawer-overlay" @click.self="emit('close')">
        <div class="drawer-panel">
          <div class="drawer-header">
            <div class="drawer-title">
              <span>{{ icon }}</span>
              <span>{{ title }}</span>
            </div>
            <button class="drawer-close" @click="emit('close')">✕</button>
          </div>
          <div class="drawer-body" :class="bodyClass">
            <slot />
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  z-index: 1000;
  display: flex;
  justify-content: flex-end;
}
.drawer-panel {
  width: min(90vw, 900px);
  height: 100%;
  background: var(--bg-primary);
  border-left: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  box-shadow: -4px 0 24px rgba(0, 0, 0, 0.4);
}
.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.drawer-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}
.drawer-close {
  width: 32px;
  height: 32px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  font-family: inherit;
}
.drawer-close:hover {
  color: var(--text-primary);
  border-color: var(--border-active);
  background: rgba(255, 255, 255, 0.05);
}
.drawer-body {
  flex: 1;
  overflow: hidden;
}
.config-drawer-body {
  overflow-y: auto;
  padding: 20px;
}

/* 抽屉过渡动画 */
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.25s ease;
}
.drawer-enter-active .drawer-panel,
.drawer-leave-active .drawer-panel {
  transition: transform 0.25s ease;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}
.drawer-enter-from .drawer-panel {
  transform: translateX(100%);
}
.drawer-leave-to .drawer-panel {
  transform: translateX(100%);
}
</style>
