import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/config', name: 'chat', component: () => import('../views/ChatConfig.vue') },
  { path: '/lab', name: 'chatlab', component: () => import('../views/ChatLab.vue') },
  { path: '/groups', name: 'groups', component: () => import('../views/GroupConfig.vue') },
  { path: '/plugins', name: 'plugins', component: () => import('../views/PluginManager.vue') },
  { path: '/logs', name: 'logs', component: () => import('../views/MessageLog.vue') },
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') },
  { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
  { path: '/:pathMatch(.*)*', name: 'notFound', component: () => import('../views/NotFound.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 鉴权状态缓存（模块变量，避免每次路由跳转重复请求；带时间戳，TTL 60 秒）
let authStatusCache = null
let cacheTs = 0
const CACHE_TTL_MS = 60000

// 供 store 在收到 401 时失效缓存：服务端中途启用 api_key 后
// 无需整页刷新即可重新拉取鉴权状态（router 不依赖 store，无循环引用）
export function invalidateAuthStatusCache() {
  authStatusCache = null
  cacheTs = 0
}

async function fetchAuthRequired() {
  if (authStatusCache !== null && Date.now() - cacheTs < CACHE_TTL_MS) {
    return authStatusCache
  }
  try {
    const res = await fetch('/api/auth/status')
    if (res.ok) {
      const data = await res.json()
      authStatusCache = !!data.auth_required
      cacheTs = Date.now()
      return authStatusCache
    }
  } catch (e) {
    // 网络异常时放行，后续请求若 401 会由 store 跳转登录页
  }
  return false
}

router.beforeEach(async (to) => {
  if (to.path === '/login') return true
  const authRequired = await fetchAuthRequired()
  const hasKey = !!localStorage.getItem('qingci_api_key')
  if (authRequired && !hasKey) return '/login'
  return true
})

export default router
