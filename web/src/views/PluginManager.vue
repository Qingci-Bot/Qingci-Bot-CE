<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<script setup>
import { ref, computed, onMounted } from 'vue';
import Drawer from '../components/Drawer.vue';
import CommandTab from '../components/plugin/CommandTab.vue';
import MarketTab from '../components/plugin/MarketTab.vue';
import PluginConfigDrawer from '../components/plugin/PluginConfigDrawer.vue';
import { useAppStore } from '../stores/app';
import { useToast } from '../composables/useToast';

const store = useAppStore();
const { showToast } = useToast();
const modulePath = ref('');
const loading = ref('');
const activeCategory = ref('all');
const expandedMetrics = ref('');
const activeTab = ref('plugins'); // 'plugins' | 'commands' | 'market'

// 插件管理页面抽屉
const drawerOpen = ref(false);
const drawerPlugin = ref(null);
const drawerPage = ref(null);

// 插件指标缓存（name -> metrics）：store.fetchStatus() 每 3 秒整体替换 plugins 数组，
// 挂在 plugin 对象上的 _metrics 会被轮询清掉，故改用组件本地 Map 保存
const metricsMap = ref(new Map());
// GET /api/plugin 返回的插件详情（含 author / load_error / pages），与 store.plugins 按 name 合并
const pluginDetails = ref([]);

// 插件配置抽屉（JSON Schema 自动生成表单，状态内聚在子组件中，经 ref.open(plugin) 打开）
const configDrawer = ref(null);

onMounted(() => {
  store.fetchStatus();
  // 额外拉取 /api/plugin 详情（author / load_error / pages），
  // 与 store.plugins（来自 /api/bot/status，无 author/load_error）按 name 合并
  fetchPluginDetails();
});

async function fetchPluginDetails() {
  try {
    pluginDetails.value = await store.apiFetch('/api/plugin');
  } catch (e) {
    // Bot 未启动等场景下该接口不可用，忽略即可（卡片仅使用 status 数据）
  }
}

const categories = computed(() => {
  const cats = new Set(mergedPlugins.value.map((p) => p.category || '未分类'));
  return ['all', ...Array.from(cats).sort()];
});

const filteredPlugins = computed(() => {
  if (activeCategory.value === 'all') return mergedPlugins.value;
  return mergedPlugins.value.filter((p) => (p.category || '未分类') === activeCategory.value);
});

// store.plugins 与 /api/plugin 详情按 name 合并：仅补充 store.plugins 缺失的
// author / load_error 字段（/api/bot/status 不含），不覆盖轮询到的实时 enabled/status；
// 并追加仅在 /api/plugin 中出现（如加载失败）的插件，保证 load_error 可展示
const mergedPlugins = computed(() => {
  const detailsByName = new Map((pluginDetails.value || []).map((d) => [d.name, d]));
  const merged = store.plugins.map((p) => {
    const detail = detailsByName.get(p.name);
    if (!detail) return p;
    return { ...p, author: detail.author, load_error: detail.load_error };
  });
  for (const d of pluginDetails.value || []) {
    // 仅追加加载失败（需展示 load_error 原因）的插件，避免把已卸载成功、
    // 已从 /api/plugin 消失的旧缓存条目重新"复活"回列表
    if (!store.plugins.some((p) => p.name === d.name) && d.load_error) merged.push(d);
  }
  return merged;
});

const drawerUrl = computed(() => {
  if (!drawerPlugin.value) return '';
  // 多页面插件优先使用后端返回的页面 URL；追加 cache-buster 强制 iframe 刷新
  const base = drawerPage.value?.url || `/api/plugin-data/${drawerPlugin.value.name}/`;
  const sep = base.includes('?') ? '&' : '?';
  // 插件静态页面已鉴权：iframe 无法携带请求头，将 api_key 作为 token 传入
  const key = store.getApiKey();
  return `${base}${sep}t=${Date.now()}${key ? `&token=${encodeURIComponent(key)}` : ''}`;
});

const statusLabel = (s) =>
  ({ loading: '加载中', loaded: '已加载', disabled: '已禁用', error: '错误', unloading: '卸载中' })[
    s
  ] || s;
const statusClass = (s) =>
  ({ loaded: 'green', loading: 'yellow', disabled: 'gray', error: 'red', unloading: 'yellow' })[
    s
  ] || 'gray';

function openDrawer(plugin, page) {
  drawerPlugin.value = plugin;
  drawerPage.value = page;
  drawerOpen.value = true;
}

function closeDrawer() {
  drawerOpen.value = false;
  drawerPlugin.value = null;
  drawerPage.value = null;
}

function openConfig(plugin) {
  configDrawer.value?.open(plugin);
}

async function reload(name) {
  loading.value = name;
  try {
    await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}/reload`, { method: 'POST' });
    await store.fetchStatus();
    showToast('success', `插件 ${name} 已重载`);
  } catch (e) {
    showToast('error', `重载失败：${e.message}`);
  } finally {
    loading.value = '';
  }
}

async function loadExternal() {
  if (!modulePath.value.trim()) return;
  loading.value = '__load__';
  try {
    await store.apiFetch('/api/plugin/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module_path: modulePath.value.trim() }),
    });
    modulePath.value = '';
    await store.fetchStatus();
    showToast('success', '插件已加载');
  } catch (e) {
    showToast('error', `加载失败：${e.message}`);
  } finally {
    loading.value = '';
  }
}

async function unload(name, purge = false) {
  const message = purge
    ? `彻底删除插件 ${name}？将删除插件文件、数据目录与第三方依赖，此操作不可恢复！`
    : `卸载插件 ${name}？将删除插件文件，保留数据与依赖（实例模式下仅卸载不删文件）。`;
  if (!window.confirm(message)) return;
  loading.value = name;
  try {
    const query = purge ? '?purge=true' : '';
    await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}${query}`, { method: 'DELETE' });
    await store.fetchStatus();
    // 同步刷新 /api/plugin 详情缓存，卸载成功的插件立即从列表消失
    await fetchPluginDetails();
    showToast('success', purge ? `插件 ${name} 已彻底删除` : `插件 ${name} 已卸载`);
  } catch (e) {
    showToast('error', `卸载失败：${e.message}`);
  } finally {
    loading.value = '';
  }
}

async function toggleEnabled(plugin) {
  loading.value = plugin.name;
  const action = plugin.enabled ? 'disable' : 'enable';
  try {
    await store.apiFetch(`/api/plugin/${encodeURIComponent(plugin.name)}/${action}`, {
      method: 'POST',
    });
    await store.fetchStatus();
    showToast('success', `插件 ${plugin.name} 已${plugin.enabled ? '禁用' : '启用'}`);
  } catch (e) {
    showToast('error', `${action === 'disable' ? '禁用' : '启用'}失败：${e.message}`);
    // 请求失败后强制刷新，恢复开关真实状态（:checked 由 plugin.enabled 驱动）
    await store.fetchStatus();
  } finally {
    loading.value = '';
  }
}

async function toggleMetrics(name) {
  if (expandedMetrics.value === name) {
    expandedMetrics.value = '';
    return;
  }
  expandedMetrics.value = name;
  try {
    const data = await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}/metrics`);
    metricsMap.value.set(name, data);
  } catch (e) {
    showToast('error', `获取指标失败：${e.message}`);
    expandedMetrics.value = '';
  }
}

function switchTab(tab) {
  activeTab.value = tab;
}
</script>

<template>
  <div class="page-header">
    <h1>插件管理</h1>
    <p>查看、重载、加载、卸载、禁用和启用 Bot 插件</p>
  </div>

  <div class="page-body">
    <!-- Tab 导航 -->
    <div class="main-tabs">
      <button
        :class="['main-tab-btn', { active: activeTab === 'plugins' }]"
        @click="switchTab('plugins')"
      >
        插件管理
      </button>
      <button
        :class="['main-tab-btn', { active: activeTab === 'commands' }]"
        @click="switchTab('commands')"
      >
        命令管理
      </button>
      <button
        :class="['main-tab-btn', { active: activeTab === 'market' }]"
        @click="switchTab('market')"
      >
        插件市场
      </button>
    </div>

    <!-- 插件管理 Tab -->
    <template v-if="activeTab === 'plugins'">
      <div class="card fade-in">
        <div class="card-header">
          <div class="card-title">加载外部插件</div>
        </div>
        <div class="input-group">
          <div class="form-group" style="flex: 1">
            <label>Python 模块路径</label>
            <input v-model="modulePath" type="text" placeholder="例如：plugins.my_plugin" />
          </div>
          <button class="btn btn-primary" :disabled="loading === '__load__'" @click="loadExternal">
            <span>＋</span> 加载
          </button>
        </div>
      </div>

      <div class="card fade-in">
        <div class="card-header">
          <div class="card-title">已加载插件</div>
          <div class="category-tabs">
            <button
              v-for="cat in categories"
              :key="cat"
              :class="['tab-btn', { active: activeCategory === cat }]"
              @click="activeCategory = cat"
            >
              {{ cat === 'all' ? '全部' : cat }}
            </button>
          </div>
        </div>
        <div v-if="filteredPlugins.length === 0" class="empty-state">
          <div class="icon">◇</div>
          <div>{{ activeCategory === 'all' ? '暂无插件' : '该分类下暂无插件' }}</div>
        </div>
        <div v-else>
          <div v-for="plugin in filteredPlugins" :key="plugin.name" class="plugin-card">
            <div class="plugin-info">
              <div class="name">
                {{ plugin.name }}
                <span class="tag tag-accent">{{ plugin.version }}</span>
                <span v-if="plugin.category" class="tag tag-purple" style="margin-left: 6px">{{
                  plugin.category
                }}</span>
                <span v-if="plugin.author" class="tag tag-blue" style="margin-left: 6px">{{
                  plugin.author
                }}</span>
              </div>
              <div class="desc">{{ plugin.description || '无描述' }}</div>
              <div v-if="plugin.load_error" class="desc load-error" :title="plugin.load_error">
                ⚠ {{ plugin.load_error }}
              </div>
              <div class="plugin-meta-row">
                <span class="status-badge" :class="statusClass(plugin.status)">
                  <span class="status-dot" :class="statusClass(plugin.status)" />
                  {{ statusLabel(plugin.status) }}
                </span>
                <button
                  class="btn-metrics"
                  :title="expandedMetrics === plugin.name ? '收起指标' : '查看指标'"
                  @click="toggleMetrics(plugin.name)"
                >
                  <span
                    :class="{
                      spin: expandedMetrics === plugin.name && !metricsMap.get(plugin.name),
                    }"
                    >⏱</span
                  >
                  指标
                </button>
              </div>
              <!-- 指标面板（数据存于组件本地 metricsMap，轮询刷新 plugins 数组不会丢失） -->
              <div
                v-if="expandedMetrics === plugin.name && metricsMap.get(plugin.name)"
                class="metrics-panel"
              >
                <div
                  v-if="metricsMap.get(plugin.name).length === 0"
                  class="empty-state"
                  style="padding: 12px"
                >
                  暂无指标数据
                </div>
                <table v-else class="metrics-table">
                  <thead>
                    <tr>
                      <th>Handler</th>
                      <th>事件</th>
                      <th>优先级</th>
                      <th>调用</th>
                      <th>平均</th>
                      <th>错误</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="m in metricsMap.get(plugin.name)" :key="m.handler">
                      <td>{{ m.handler }}</td>
                      <td>
                        <span class="tag tag-accent">{{ m.event_type }}</span>
                      </td>
                      <td>{{ m.priority }}</td>
                      <td>{{ m.call_count }}</td>
                      <td>{{ m.avg_time_ms }}ms</td>
                      <td :class="{ 'text-danger': m.error_count > 0 }">{{ m.error_count }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="action-bar">
              <label class="switch" :title="plugin.enabled ? '点击禁用' : '点击启用'">
                <input
                  type="checkbox"
                  :checked="plugin.enabled"
                  :disabled="loading === plugin.name"
                  @change="toggleEnabled(plugin)"
                />
                <span class="slider round" />
              </label>
              <template v-if="plugin.pages && plugin.pages.length > 0">
                <button
                  v-for="page in plugin.pages"
                  :key="page.title"
                  class="btn btn-accent btn-sm"
                  @click="openDrawer(plugin, page)"
                >
                  <span>{{ page.icon || '◇' }}</span> {{ page.title }}
                </button>
              </template>
              <button
                class="btn btn-secondary btn-sm"
                :disabled="loading === plugin.name"
                @click="reload(plugin.name)"
              >
                <span :class="{ spin: loading === plugin.name }">↻</span> 重载
              </button>
              <button
                class="btn btn-secondary btn-sm"
                :disabled="loading === plugin.name"
                @click="openConfig(plugin)"
              >
                <span>⚙</span> 配置
              </button>
              <button
                class="btn btn-danger btn-sm"
                :disabled="loading === plugin.name"
                @click="unload(plugin.name)"
              >
                卸载
              </button>
              <button
                class="btn btn-danger btn-sm"
                :disabled="loading === plugin.name"
                @click="unload(plugin.name, true)"
              >
                彻底删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 插件管理页面抽屉 -->
      <Drawer
        :open="drawerOpen"
        :icon="drawerPage?.icon || '◇'"
        :title="`${drawerPlugin?.name} - ${drawerPage?.title}`"
        @close="closeDrawer"
      >
        <iframe
          :key="drawerUrl"
          :src="drawerUrl"
          class="drawer-iframe"
          sandbox="allow-scripts allow-same-origin allow-forms"
          title="插件管理页面"
        />
      </Drawer>

      <!-- 插件配置抽屉（JSON Schema 自动生成表单） -->
      <PluginConfigDrawer ref="configDrawer" />
    </template>

    <!-- 命令管理 Tab -->
    <CommandTab v-if="activeTab === 'commands'" />

    <!-- 插件市场 Tab -->
    <MarketTab v-if="activeTab === 'market'" />
  </div>
</template>

<style scoped>
/* Tab 导航（下划线式）：主 Tab 与分类 Tab 统一视觉 */
.main-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 22px;
  border-bottom: 1px solid var(--border-color);
}
.main-tab-btn,
.category-tabs .tab-btn {
  padding: 10px 20px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.2s;
  font-family: inherit;
}
.main-tab-btn:hover,
.category-tabs .tab-btn:hover {
  color: var(--text-primary);
}
.main-tab-btn.active,
.category-tabs .tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

/* 分类标签容器（下划线式，与主 Tab 同视觉） */
.category-tabs {
  display: flex;
  gap: 2px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--border-color);
}

/* 分类按钮基础视觉（与市场标签同源，见 MarketTab.vue 的 .tab-btn） */
.tab-btn {
  padding: 4px 12px;
  border-radius: 20px;
  border: 1px solid var(--border-color);
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}
.tab-btn:hover {
  border-color: var(--border-active);
  color: var(--text-primary);
}
.tab-btn.active {
  background: var(--accent-bg);
  color: var(--accent);
  border-color: rgba(251, 191, 36, 0.3);
}

/* 插件元信息行 */
.plugin-meta-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}
.btn-metrics {
  padding: 2px 10px;
  border-radius: 12px;
  border: 1px solid var(--border-color);
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
}
.btn-metrics:hover {
  color: var(--text-primary);
  border-color: var(--border-active);
}

/* 指标面板 */
.metrics-panel {
  margin-top: 10px;
  padding: 10px;
  background: rgba(0, 0, 0, 0.2);
  border-radius: var(--radius-xs);
  border: 1px solid var(--border-color);
}
.metrics-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.metrics-table th,
.metrics-table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}
.metrics-table th {
  color: var(--text-muted);
  font-weight: 500;
  font-size: 11px;
}
.metrics-table td {
  color: var(--text-secondary);
}
.text-danger {
  color: var(--danger) !important;
}

/* 插件管理页面抽屉 */
.drawer-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: var(--bg-primary);
}

/* 插件加载失败原因 */
.load-error {
  color: var(--danger);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.spin {
  display: inline-block;
  animation: pm-spin 1s linear infinite;
}
@keyframes pm-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
