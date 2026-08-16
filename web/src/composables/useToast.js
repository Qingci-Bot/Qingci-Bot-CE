import { ref } from 'vue'

// 模块级单例：所有组件共享同一个 toast 状态，由 App.vue 统一渲染顶部悬浮通知
const toast = ref({ show: false, type: 'info', message: '' })
let timer = null

export function showToast(type, message) {
  toast.value = { show: true, type, message }
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => { toast.value.show = false }, 4000)
}

export function useToast() {
  return { toast, showToast }
}
