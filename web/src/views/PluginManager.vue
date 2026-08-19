<script setup>
import { ref, computed, onMounted } from 'vue';
import Drawer from '../components/Drawer.vue';
import { useAppStore } from '../stores/app';
import { useToast } from '../composables/useToast';

const store = useAppStore();
const { showToast } = useToast();
const modulePath = ref('');
const loading = ref('');
const activeCategory = ref('all');
const expandedMetrics = ref('');
const activeTab = ref('plugins'); // 'plugins' | 'commands' | 'market'

// 插件市场
const market = ref([]);
const marketInfo = ref(null);
const marketLoading = ref(false);
const marketSearch = ref('');
const marketTag = ref('all');
const marketAction = ref(''); // 'install:name' | 'update:name' | 'refresh' | 'uninstall:name'
const marketError = ref('');
const marketSource = ref(''); // 当前市场源
const marketDefaultSource = ref(''); // 官方默认市场源
const marketSourceInput = ref(''); // 自定义源输入框
const showSourcePanel = ref(false);
const sourceSaving = ref(false);

// 命令管理
const commands = ref([]);
const commandLoading = ref('');

// 插件管理页面抽屉
const drawerOpen = ref(false);
const drawerPlugin = ref(null);
const drawerPage = ref(null);

// 插件指标缓存（name -> metrics）：store.fetchStatus() 每 3 秒整体替换 plugins 数组，
// 挂在 plugin 对象上的 _metrics 会被轮询清掉，故改用组件本地 Map 保存
const metricsMap = ref(new Map());
// GET /api/plugin 返回的插件详情（含 author / load_error / pages），与 store.plugins 按 name 合并
const pluginDetails = ref([]);
// 插件市场请求序号：快速切换 Tab 时丢弃过期响应，避免竞态覆盖
let marketSeq = 0;

// 插件配置抽屉（JSON Schema 自动生成表单）
const configOpen = ref(false);
const configPlugin = ref(null);
const configSchema = ref(null);
const configValues = ref({});
const configSaving = ref(false);
const configLoading = ref(false);

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
    if (!store.plugins.some((p) => p.name === d.name)) merged.push(d);
  }
  return merged;
});

const drawerUrl = computed(() => {
  if (!drawerPlugin.value) return '';
  // 多页面插件优先使用后端返回的页面 URL；追加 cache-buster 强制 iframe 刷新
  const base = drawerPage.value?.url || `/api/plugin-data/${drawerPlugin.value.name}/`;
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}t=${Date.now()}`;
});

const statusLabel = (s) =>
  ({ loading: '加载中', loaded: '已加载', disabled: '已禁用', error: '错误', unloading: '卸载中' })[
    s
  ] || s;
const statusClass = (s) =>
  ({ loaded: 'green', loading: 'yellow', disabled: 'gray', error: 'red', unloading: 'yellow' })[
    s
  ] || 'gray';

// 权限等级中文映射（含组合标签，如 "(SUPERUSER & PRIVATE)"）
const permZh = {
  SUPERUSER: '超级管理员',
  ADMIN: '管理员',
  EVERYONE: '所有人',
  MEMBER: '群成员',
  PRIVATE: '私聊',
  GROUP: '群聊',
  CUSTOM: '自定义',
};
const permissionLabel = (p) => {
  if (!p) return '-';
  let s = String(p);
  for (const [k, v] of Object.entries(permZh)) s = s.replaceAll(k, v);
  return s;
};

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

function closeConfig() {
  configOpen.value = false;
  configPlugin.value = null;
  configSchema.value = null;
  configValues.value = {};
}

async function openConfig(plugin) {
  configPlugin.value = plugin;
  configOpen.value = true;
  configLoading.value = true;
  try {
    const data = await store.apiFetch(`/api/plugin/${encodeURIComponent(plugin.name)}/config`);
    configSchema.value = data.schema;
    // 以 schema 默认值补全，保证必填字段有初值
    const merged = {};
    const props = data.schema?.properties || {};
    for (const key of Object.keys(props)) {
      merged[key] = props[key].default !== undefined ? props[key].default : '';
    }
    Object.assign(merged, data.values || {});
    configValues.value = merged;
  } catch (e) {
    showToast('error', `获取配置失败：${e.message}`);
    closeConfig();
  } finally {
    configLoading.value = false;
  }
}

const configFields = computed(() => {
  if (!configSchema.value?.properties) return [];
  const required = configSchema.value.required || [];
  return Object.entries(configSchema.value.properties).map(([key, prop]) => ({
    key,
    title: prop.title || key,
    type: prop.type || 'string',
    description: prop.description || '',
    required: required.includes(key),
    default: prop.default,
  }));
});

function configInputType(field) {
  if (field.type === 'boolean') return 'checkbox';
  if (field.type === 'integer' || field.type === 'number') return 'number';
  return 'text';
}

async function saveConfig() {
  configSaving.value = true;
  try {
    const res = await store.apiFetch(
      `/api/plugin/${encodeURIComponent(configPlugin.value.name)}/config`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values: configValues.value }),
      },
    );
    configValues.value = res;
    showToast('success', `插件 ${configPlugin.value.name} 配置已保存`);
  } catch (e) {
    showToast('error', `保存失败：${e.message}`);
  } finally {
    configSaving.value = false;
  }
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

async function unload(name) {
  loading.value = name;
  try {
    await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}`, { method: 'DELETE' });
    await store.fetchStatus();
    showToast('success', `插件 ${name} 已卸载`);
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

async function fetchCommands() {
  try {
    commands.value = await store.apiFetch('/api/command/conflicts');
  } catch (e) {
    // 失败置 null，模板显示错误态而非"暂无命令"
    commands.value = null;
    showToast('error', `获取命令列表失败：${e.message}`);
  }
}

async function toggleCommand(cmd) {
  commandLoading.value = `${cmd.plugin}/${cmd.command}`;
  try {
    const body = { disabled: !cmd.disabled };
    const res = await store.apiFetch(
      `/api/command/${encodeURIComponent(cmd.plugin)}/${encodeURIComponent(cmd.command)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
    cmd.disabled = res.disabled;
    showToast('success', `命令 ${cmd.plugin}/${cmd.command} 已${cmd.disabled ? '禁用' : '启用'}`);
  } catch (e) {
    showToast('error', `操作失败：${e.message}`);
  } finally {
    commandLoading.value = '';
  }
}

async function updatePriority(cmd, newPriority) {
  const val = parseInt(newPriority);
  if (isNaN(val) || val < 0 || val > 100) return;
  commandLoading.value = `${cmd.plugin}/${cmd.command}`;
  try {
    const res = await store.apiFetch(
      `/api/command/${encodeURIComponent(cmd.plugin)}/${encodeURIComponent(cmd.command)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ priority: val }),
      },
    );
    cmd.priority = res.priority;
    showToast('success', `优先级已更新为 ${val}`);
  } catch (e) {
    showToast('error', `更新失败：${e.message}`);
  } finally {
    commandLoading.value = '';
  }
}

function switchTab(tab) {
  activeTab.value = tab;
  if (tab === 'commands') fetchCommands();
  if (tab === 'market') fetchMarket();
}

// ---- 插件市场 ----

const filteredMarket = computed(() => {
  const q = marketSearch.value.trim().toLowerCase();
  const tag = marketTag.value;
  return market.value.filter((p) => {
    const matchTag = tag === 'all' || (p.tags || []).includes(tag);
    if (!matchTag) return false;
    if (!q) return true;
    return (
      (p.name || '').toLowerCase().includes(q) ||
      (p.title || '').toLowerCase().includes(q) ||
      (p.description || '').toLowerCase().includes(q) ||
      (p.tags || []).some((t) => t.toLowerCase().includes(q))
    );
  });
});

const marketTags = computed(() => {
  const set = new Set();
  for (const p of market.value) for (const t of p.tags || []) set.add(t);
  return Array.from(set).sort();
});

const marketStats = computed(() => {
  const installed = market.value.filter((p) => p.installed).length;
  const updatable = market.value.filter((p) => p.update_available).length;
  return { total: market.value.length, installed, updatable };
});

const marketTypeLabel = (t) => ({ sdk: 'SDK', builtin: '内置' })[t] || t || 'SDK';

const marketUpdatedText = computed(() => {
  const ts = marketInfo.value?.fetched_at;
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
});

async function fetchMarket() {
  const seq = ++marketSeq;
  marketLoading.value = true;
  marketError.value = '';
  try {
    const [items, info, source] = await Promise.all([
      store.apiFetch('/api/plugins/market'),
      store.apiFetch('/api/plugins/market/info'),
      store.apiFetch('/api/plugins/market/source'),
    ]);
    if (seq !== marketSeq) return; // 已发起更新的请求，丢弃过期响应
    market.value = items;
    marketInfo.value = info;
    marketSource.value = source.url || '';
    marketDefaultSource.value = source.default_url || '';
  } catch (e) {
    if (seq !== marketSeq) return;
    marketError.value = e.message || '获取市场失败';
    showToast('error', `获取插件市场失败：${e.message}`);
  } finally {
    if (seq === marketSeq) marketLoading.value = false;
  }
}

async function marketInstall(name) {
  marketAction.value = `install:${name}`;
  try {
    await store.apiFetch('/api/plugins/market/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    showToast('success', `插件 ${name} 安装成功`);
    await store.fetchStatus();
    await fetchMarket();
  } catch (e) {
    showToast('error', `安装失败：${e.message}`);
  } finally {
    marketAction.value = '';
  }
}

async function marketUpdate(name) {
  marketAction.value = `update:${name}`;
  try {
    await store.apiFetch('/api/plugins/market/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    showToast('success', `插件 ${name} 更新成功`);
    await store.fetchStatus();
    await fetchMarket();
  } catch (e) {
    showToast('error', `更新失败：${e.message}`);
  } finally {
    marketAction.value = '';
  }
}

async function marketRefresh() {
  marketAction.value = 'refresh';
  try {
    const res = await store.apiFetch('/api/plugins/market/refresh', { method: 'POST' });
    showToast('success', res.message || '市场已刷新');
    await fetchMarket();
  } catch (e) {
    showToast('error', `刷新失败：${e.message}`);
  } finally {
    marketAction.value = '';
  }
}

async function marketToggleSource() {
  showSourcePanel.value = !showSourcePanel.value;
  if (showSourcePanel.value) {
    marketSourceInput.value = marketSource.value || marketDefaultSource.value || '';
  } else {
    marketSourceInput.value = '';
  }
}

async function marketApplySource() {
  const url = (marketSourceInput.value || '').trim();
  if (!url) {
    showToast('error', '请输入市场源地址');
    return;
  }
  sourceSaving.value = true;
  try {
    const res = await store.apiFetch('/api/plugins/market/source', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    showToast('success', res.message || '市场源已切换');
    marketSource.value = res.url || url;
    showSourcePanel.value = false;
    marketSourceInput.value = '';
    await fetchMarket();
  } catch (e) {
    showToast('error', `切换失败：${e.message}`);
  } finally {
    sourceSaving.value = false;
  }
}

async function marketUninstall(name) {
  marketAction.value = `uninstall:${name}`;
  try {
    await store.apiFetch(`/api/plugin/${encodeURIComponent(name)}`, { method: 'DELETE' });
    showToast('success', `插件 ${name} 已卸载`);
    await store.fetchStatus();
    await fetchMarket();
  } catch (e) {
    showToast('error', `卸载失败：${e.message}`);
  } finally {
    marketAction.value = '';
  }
}

function openHomepage(url) {
  if (url) window.open(url, '_blank', 'noopener');
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
                    :class="{ spin: expandedMetrics === plugin.name && !metricsMap.get(plugin.name) }"
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
      <Drawer
        :open="configOpen"
        icon="⚙"
        :title="`${configPlugin?.name} - 配置`"
        body-class="config-drawer-body"
        @close="closeConfig"
      >
        <div v-if="configLoading" class="empty-state">加载配置中...</div>
        <div v-else-if="!configSchema" class="empty-state">
          <div class="icon">◇</div>
          <div>该插件未定义配置项（无 Config 内嵌类）</div>
        </div>
        <div v-else class="config-form">
          <div v-if="configFields.length === 0" class="empty-state">该插件无配置字段</div>
          <div v-for="field in configFields" :key="field.key" class="config-field">
            <label class="config-label">
              <span class="config-title">
                {{ field.title }}
                <span v-if="field.required" class="required-mark" title="必填">*</span>
              </span>
              <span v-if="field.description" class="config-desc">{{ field.description }}</span>
            </label>
            <label v-if="field.type === 'boolean'" class="switch">
              <input v-model="configValues[field.key]" type="checkbox" />
              <span class="slider round" />
            </label>
            <input
              v-else
              v-model="configValues[field.key]"
              :type="configInputType(field)"
              class="config-input"
              :step="field.type === 'number' ? '0.1' : undefined"
            />
          </div>
          <div class="config-actions">
            <button class="btn btn-secondary" :disabled="configSaving" @click="closeConfig">
              取消
            </button>
            <button class="btn btn-primary" :disabled="configSaving" @click="saveConfig">
              <span :class="{ spin: configSaving }">✓</span> 保存配置
            </button>
          </div>
        </div>
      </Drawer>
    </template>

    <!-- 命令管理 Tab -->
    <template v-if="activeTab === 'commands'">
      <div class="card fade-in">
        <div class="card-header">
          <div class="card-title">已注册命令</div>
          <span class="text-muted">共 {{ commands?.length ?? 0 }} 条</span>
        </div>
        <div v-if="commands === null" class="empty-state">
          <div class="icon">⚠</div>
          <div>命令列表加载失败</div>
          <button class="btn btn-secondary btn-sm" style="margin-top: 12px" @click="fetchCommands">
            <span>↻</span> 重试
          </button>
        </div>
        <div v-else-if="commands.length === 0" class="empty-state">
          <div class="icon">◇</div>
          <div>暂无命令</div>
        </div>
        <div v-else class="command-table-wrap">
          <table class="command-table">
            <thead>
              <tr>
                <th>命令</th>
                <th>插件</th>
                <th>事件</th>
                <th>权限</th>
                <th>优先级</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(cmd, idx) in commands"
                :key="idx"
                :class="{ 'conflict-row': cmd.has_conflict }"
              >
                <td>
                  <span class="cmd-name">/{{ cmd.command }}</span>
                  <span v-if="cmd.has_conflict" class="conflict-badge" title="存在同名命令冲突"
                    >⚠</span
                  >
                </td>
                <td>
                  <span class="tag tag-accent">{{ cmd.plugin }}</span>
                </td>
                <td>
                  <span class="tag tag-purple">{{ cmd.event_type }}</span>
                </td>
                <td>
                  <span class="tag tag-perm">{{ permissionLabel(cmd.permission) }}</span>
                </td>
                <td>
                  <input
                    type="number"
                    class="priority-input"
                    :value="cmd.priority"
                    min="0"
                    max="100"
                    @change="updatePriority(cmd, $event.target.value)"
                  />
                </td>
                <td>
                  <span :class="['status-tag', cmd.disabled ? 'status-off' : 'status-on']">
                    {{ cmd.disabled ? '已禁用' : '启用' }}
                  </span>
                </td>
                <td>
                  <button
                    class="btn btn-sm"
                    :class="cmd.disabled ? 'btn-success' : 'btn-warning'"
                    :disabled="commandLoading === `${cmd.plugin}/${cmd.command}`"
                    @click="toggleCommand(cmd)"
                  >
                    {{ cmd.disabled ? '启用' : '禁用' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- 插件市场 Tab -->
    <template v-if="activeTab === 'market'">
      <div class="card fade-in">
        <div class="card-header">
          <div class="card-title">
            {{ marketInfo?.name || '插件市场' }}
            <span class="text-muted" style="margin-left: 10px">
              共 {{ marketStats.total }} 个插件 · 已装 {{ marketStats.installed }} · 可更新
              {{ marketStats.updatable }}
            </span>
            <span v-if="marketUpdatedText" class="text-muted" style="margin-left: 8px">
              索引更新于 {{ marketUpdatedText }}
            </span>
          </div>
          <div class="market-tools">
            <input
              v-model="marketSearch"
              type="text"
              class="market-search"
              placeholder="搜索插件名 / 描述 / 标签..."
            />
            <button
              class="btn btn-secondary btn-sm"
              :disabled="marketAction === 'refresh'"
              @click="marketRefresh"
            >
              <span :class="{ spin: marketAction === 'refresh' }">↻</span> 刷新市场
            </button>
            <button class="btn btn-secondary btn-sm" @click="marketToggleSource">
              <span>⚙</span> 切换源
            </button>
          </div>
        </div>
        <div v-if="showSourcePanel" class="source-panel fade-in">
          <div class="source-current">
            <span class="source-label">当前市场源</span>
            <span class="source-url" :title="marketSource">{{ marketSource || '—' }}</span>
            <button
              v-if="marketSource && marketSource !== marketDefaultSource"
              class="btn btn-secondary btn-sm"
              :disabled="sourceSaving"
              @click="
                marketSourceInput = marketDefaultSource;
                marketApplySource();
              "
            >
              恢复官方默认
            </button>
          </div>
          <div class="source-input-row">
            <input
              v-model="marketSourceInput"
              type="text"
              class="market-search"
              placeholder="输入新版市场源地址（git 仓库或 HTTP 索引）"
              @keyup.enter="marketApplySource"
            />
            <button
              class="btn btn-primary btn-sm"
              :disabled="sourceSaving"
              @click="marketApplySource"
            >
              <span :class="{ spin: sourceSaving }">⇄</span>
              {{ sourceSaving ? '切换中...' : '应用' }}
            </button>
          </div>
          <div class="source-hint">
            支持 git 仓库（如 <code>https://github.com/Qingci-Bot/Plugin-Market.git</code>） 或 HTTP
            索引 JSON 地址。官方默认：<code>{{ marketDefaultSource }}</code>
          </div>
        </div>
        <div v-if="marketTags.length > 0" class="market-tag-row">
          <button :class="['tab-btn', { active: marketTag === 'all' }]" @click="marketTag = 'all'">
            全部
          </button>
          <button
            v-for="t in marketTags"
            :key="t"
            :class="['tab-btn', { active: marketTag === t }]"
            @click="marketTag = t"
          >
            {{ t }}
          </button>
        </div>
        <div v-if="marketLoading" class="empty-state">加载市场中...</div>
        <div v-else-if="marketError" class="empty-state">
          <div class="icon">⚠</div>
          <div>市场加载失败：{{ marketError }}</div>
          <button class="btn btn-secondary btn-sm" style="margin-top: 12px" @click="fetchMarket">
            <span>↻</span> 重试
          </button>
        </div>
        <div v-else-if="filteredMarket.length === 0" class="empty-state">
          <div class="icon">◇</div>
          <div>{{ marketSearch || marketTag !== 'all' ? '未找到匹配的插件' : '市场暂无插件' }}</div>
        </div>
        <div v-else>
          <div v-for="item in filteredMarket" :key="item.name" class="market-card">
            <div class="market-info">
              <div class="name">
                <span class="market-icon">{{ item.icon || '📦' }}</span>
                {{ item.title || item.name }}
                <span class="tag tag-purple">{{ marketTypeLabel(item.type) }}</span>
                <span
                  v-for="t in item.tags"
                  :key="t"
                  class="tag tag-accent"
                  style="margin-left: 4px"
                  >{{ t }}</span
                >
              </div>
              <div class="desc">{{ item.description || '无描述' }}</div>
              <div v-if="item.requirements && item.requirements.length" class="market-meta-row">
                <span v-for="r in item.requirements" :key="r" class="tag tag-blue" title="依赖"
                  >⬡ {{ r }}</span
                >
              </div>
              <div class="market-meta-row">
                <span class="tag tag-blue">{{ item.name }}</span>
                <span class="tag tag-accent">v{{ item.version }}</span>
                <span v-if="item.author" class="text-muted">{{ item.author }}</span>
                <span v-if="item.updated_at" class="text-muted">更新 {{ item.updated_at }}</span>
                <a
                  v-if="item.homepage"
                  href="javascript:void(0)"
                  class="market-link"
                  @click="openHomepage(item.homepage)"
                  >🔗 主页</a
                >
                <span v-if="item.installed" class="status-badge green">
                  已安装 v{{ item.installed_version }}
                </span>
                <span v-if="item.update_available" class="status-badge yellow">
                  可更新至 v{{ item.version }}
                </span>
                <span
                  v-if="item.compatible === false"
                  class="status-badge badge-incompatible"
                  title="当前 Python 版本不满足插件声明的 python_requires"
                  >⚠ 不兼容当前 Python</span
                >
              </div>
            </div>
            <div class="action-bar">
              <button
                v-if="item.update_available"
                class="btn btn-primary btn-sm"
                :disabled="marketAction === `update:${item.name}`"
                @click="marketUpdate(item.name)"
              >
                <span :class="{ spin: marketAction === `update:${item.name}` }">↻</span>
                {{ marketAction === `update:${item.name}` ? '更新中...' : '更新' }}
              </button>
              <button
                v-else
                class="btn btn-primary btn-sm"
                :disabled="
                  marketAction === `install:${item.name}` ||
                  item.installed ||
                  item.compatible === false
                "
                @click="marketInstall(item.name)"
              >
                <span :class="{ spin: marketAction === `install:${item.name}` }">＋</span>
                {{
                  item.compatible === false
                    ? '不兼容'
                    : item.installed
                      ? '已安装'
                      : marketAction === `install:${item.name}`
                        ? '安装中...'
                        : '安装'
                }}
              </button>
              <button
                v-if="item.installed"
                class="btn btn-danger btn-sm"
                :disabled="marketAction === `uninstall:${item.name}`"
                @click="marketUninstall(item.name)"
              >
                <span :class="{ spin: marketAction === `uninstall:${item.name}` }">✕</span>
                {{ marketAction === `uninstall:${item.name}` ? '卸载中...' : '卸载' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>
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

/* 市场标签筛选（胶囊式） */
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

/* 插件配置表单 */
.config-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.config-field {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border-color);
}
.config-label {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.config-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}
.required-mark {
  color: var(--danger);
}
.config-desc {
  font-size: 12px;
  color: var(--text-muted);
}
.config-input {
  width: 220px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: rgba(0, 0, 0, 0.2);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  flex-shrink: 0;
}
.config-input:focus {
  outline: none;
  border-color: var(--accent);
}
.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}

/* 命令管理表格 */
.command-table-wrap {
  overflow-x: auto;
}
.command-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.command-table th,
.command-table td {
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}
.command-table th {
  color: var(--text-muted);
  font-weight: 500;
  font-size: 12px;
  white-space: nowrap;
}
.command-table td {
  color: var(--text-secondary);
}
.command-table .conflict-row {
  background: rgba(248, 113, 113, 0.08);
}
.command-table .conflict-row td:first-child {
  border-left: 3px solid var(--danger);
}
.cmd-name {
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  font-weight: 500;
  color: var(--text-primary);
}
.conflict-badge {
  margin-left: 6px;
  font-size: 14px;
  color: var(--danger);
  cursor: help;
}
.priority-input {
  width: 52px;
  padding: 3px 6px;
  border-radius: 4px;
  border: 1px solid var(--border-color);
  background: rgba(0, 0, 0, 0.2);
  color: var(--text-primary);
  font-size: 12px;
  text-align: center;
  font-family: inherit;
}
.priority-input:focus {
  outline: none;
  border-color: var(--accent);
}
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}
.status-on {
  background: rgba(52, 211, 153, 0.15);
  color: var(--success);
}
.status-off {
  background: rgba(148, 163, 184, 0.15);
  color: var(--text-muted);
}

/* 插件市场 */
.market-tools {
  display: flex;
  align-items: center;
  gap: 10px;
}
.market-search {
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: rgba(0, 0, 0, 0.2);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  max-width: 240px;
}
.market-search:focus {
  outline: none;
  border-color: var(--accent);
}
.market-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xs);
  margin-bottom: 10px;
  background: rgba(255, 255, 255, 0.02);
  transition: border-color 0.2s;
}
.market-card:hover {
  border-color: var(--border-active);
}
.market-info {
  flex: 1;
  min-width: 0;
}
.market-icon {
  display: inline-block;
  margin-right: 6px;
  font-size: 15px;
  vertical-align: -2px;
}
.market-tag-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.market-link {
  color: var(--accent);
  cursor: pointer;
  text-decoration: none;
}
.market-link:hover {
  text-decoration: underline;
}
.market-meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.badge-incompatible {
  background: var(--danger-bg);
  color: var(--danger);
  border-color: rgba(248, 113, 113, 0.25);
}
.load-error {
  color: var(--danger);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.market-card .action-bar {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.spin {
  display: inline-block;
  animation: market-spin 1s linear infinite;
}
@keyframes market-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* 市场源切换面板 */
.source-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  margin-bottom: 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xs);
  background: rgba(255, 255, 255, 0.02);
}
.source-current {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.source-label {
  font-size: 12px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.source-url {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
  max-width: 480px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.source-input-row .market-search {
  flex: 1;
  max-width: 100%;
}
.source-hint {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.7;
}
.source-hint code {
  font-family: var(--font-mono);
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.05);
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
