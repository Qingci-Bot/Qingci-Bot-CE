import { ref, onUnmounted } from 'vue'

export function useToast() {
  const toast = ref({ show: false, type: 'info', message: '' })
  let timer = null
  function showToast(type, message) {
    toast.value = { show: true, type, message }
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { toast.value.show = false }, 4000)
  }
  onUnmounted(() => { if (timer) clearTimeout(timer) })
  return { toast, showToast }
}
