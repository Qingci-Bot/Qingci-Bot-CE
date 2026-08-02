import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/config', name: 'chat', component: () => import('../views/ChatConfig.vue') },
  { path: '/plugins', name: 'plugins', component: () => import('../views/PluginManager.vue') },
  { path: '/logs', name: 'logs', component: () => import('../views/MessageLog.vue') },
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})