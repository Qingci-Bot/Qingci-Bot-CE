<!--
  Copyright (C) 2026 Zhou Zhe (aka luoqingci)
  SPDX-License-Identifier: GPL-3.0-or-later
  Additional terms under GPLv3 Section 7: see NOTICE
-->

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useAppStore } from '../../stores/app';
import { useToast } from '../../composables/useToast';

const store = useAppStore();
const { showToast } = useToast();

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
// 市场请求序号：快速切换 Tab 时丢弃过期响应，避免竞态覆盖
let marketSeq = 0;

// 组件随 Tab 切换挂载（父级 v-if），每次进入市场页自动拉取
onMounted(fetchMarket);

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
  if (
    !window.confirm(
      `卸载插件 ${name}？将删除插件文件，保留数据与依赖（实例模式下仅卸载不删文件）。`,
    )
  )
    return;
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
        <button class="btn btn-primary btn-sm" :disabled="sourceSaving" @click="marketApplySource">
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
            <span v-for="t in item.tags" :key="t" class="tag tag-accent" style="margin-left: 4px">{{
              t
            }}</span>
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
              marketAction === `install:${item.name}` || item.installed || item.compatible === false
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

<style scoped>
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
