<script setup>
import { ref, computed, onMounted } from 'vue';
import { useAppStore } from '../stores/app';
import { useToast } from '../composables/useToast';

const store = useAppStore();
const { showToast } = useToast();

// 平台选项（与后端 SUPPORTED_PLATFORMS 保持一致）
const platformOptions = [
  {
    value: 'onebot',
    label: 'OneBot 11',
    hint: '反向 WebSocket，对接 OneBot 11 协议端（如 LLBot / NapCat）',
  },
  {
    value: 'onebot12',
    label: 'OneBot 12',
    hint: '原生反向 WebSocket，对接 OneBot 12 协议端（如 NapCat / Lagrange.OneBot）',
  },
  { value: 'telegram', label: 'Telegram', hint: 'Bot API 长轮询（创建后在设置中填写 token）' },
];

function platformLabel(value) {
  const opt = platformOptions.find((o) => o.value === value);
  return opt ? opt.label : value;
}

// 适配器摘要（后端 instance_adapters 返回）→ 标签列表
const adapterLabels = [
  { key: 'onebot', label: 'OneBot 11' },
  { key: 'onebot12', label: 'OneBot 12' },
  { key: 'telegram', label: 'Telegram' },
];

function enabledAdapters(adapters) {
  if (!adapters) return [];
  return adapterLabels.filter((a) => adapters[a.key]);
}

// 字节数 → 人类可读
function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let size = v;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i++;
  }
  return `${size >= 100 || i === 0 ? Math.round(size) : size.toFixed(1)} ${units[i]}`;
}

const showCreateForm = ref(false);
const createName = ref('');
const createPlatform = ref('onebot');
const createDescription = ref('');
const createPort = ref('');

// 当前激活实例
const activeInstance = computed(() => store.currentInstance);

onMounted(async () => {
  await store.fetchInstances();
});

function openCreateForm() {
  createName.value = '';
  createPlatform.value = 'onebot';
  createDescription.value = '';
  createPort.value = '';
  showCreateForm.value = true;
}

function cancelCreate() {
  showCreateForm.value = false;
}

function doCreate() {
  const name = createName.value.trim();
  if (!name) return;
  showToast('info', '正在创建实例...');
  const payload = { name, platform: createPlatform.value };
  const desc = createDescription.value.trim();
  if (desc) payload.description = desc;
  const port = Number(createPort.value);
  if (createPort.value.trim() && port >= 1024 && port <= 65535) {
    payload.port = port;
  }
  store
    .createInstance(payload)
    .then(() => {
      showCreateForm.value = false;
      showToast('success', `实例「${name}」已创建`);
    })
    .then(() => store.fetchInstances())
    .catch((e) => showToast('error', e.message || '创建失败'));
}

function onSwitch(inst) {
  if (inst.running) return;
  if (window.confirm(`切换到实例「${inst.name}」？应用将重启以加载该实例。`)) {
    store.switchInstance(inst.name);
  }
}

function onDelete(inst) {
  if (inst.running) return;
  if (
    window.confirm(`删除实例「${inst.name}」及其 config/data/plugins 全部数据？此操作不可恢复。`)
  ) {
    store
      .deleteInstance(inst.name)
      .then(() => showToast('success', `实例「${inst.name}」已删除`))
      .catch((e) => showToast('error', e.message || '删除失败'));
  }
}

function onRename(inst) {
  const newName = window.prompt(`将实例「${inst.name}」重命名为（字母/数字/-/_）`, inst.name);
  if (!newName || !newName.trim() || newName.trim() === inst.name) return;
  if (inst.running) {
    showToast('info', `正在重命名并重启到「${newName.trim()}」...`);
    store.renameInstance(inst.name, newName.trim()).catch(() => {});
    return;
  }
  store
    .renameInstance(inst.name, newName.trim())
    .then(() => showToast('success', `已重命名为「${newName.trim()}」`))
    .catch((e) => showToast('error', e.message || '重命名失败'));
}
</script>

<template>
  <div class="page-header">
    <h1>实例管理</h1>
    <p>创建、切换、重命名或删除多平台运行实例</p>
  </div>

  <div class="page-body">
    <!-- 当前激活实例 -->
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">当前实例</div>
      </div>
      <div v-if="activeInstance" class="active-instance-card">
        <div class="active-instance-row">
          <span class="status-dot green" />
          <span class="active-instance-name">{{ activeInstance.name }}</span>
          <span class="instance-badge">{{ platformLabel(activeInstance.platform) }}</span>
          <span class="active-instance-running">运行中</span>
        </div>
        <div class="active-instance-meta">
          <span class="meta-item" :title="`端口 ${activeInstance.port}`">
            <span class="meta-label">端口</span>{{ activeInstance.port }}
          </span>
          <span class="meta-item" :title="'data 目录占用'">
            <span class="meta-label">数据</span>{{ fmtBytes(activeInstance.disk_usage) }}
          </span>
          <span v-if="activeInstance.description" class="meta-item">
            <span class="meta-label">描述</span>{{ activeInstance.description }}
          </span>
        </div>
        <div class="hint-text" style="margin-top: 10px">
          实例目录：<code>instances/{{ activeInstance.name }}/</code><br />
          可在「系统设置」中配置该平台的连接参数。
        </div>
      </div>
      <div v-else class="empty-state" style="padding: 20px">
        <div class="icon">◉</div>
        <div>暂无运行中的实例</div>
        <div style="font-size: 12px; margin-top: 6px">请先创建并启动一个实例</div>
      </div>
    </div>

    <!-- 实例列表 -->
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">全部实例</div>
        <div class="action-bar">
          <button class="btn btn-secondary btn-sm" @click="store.fetchInstances">
            <span style="display: inline-block" :class="{ spin: store.loading }">↻</span> 刷新
          </button>
          <button class="btn btn-primary btn-sm" @click="openCreateForm">＋ 新建实例</button>
        </div>
      </div>

      <!-- 创建表单（内联） -->
      <div v-if="showCreateForm" class="create-form-inline">
        <div class="create-form-row">
          <input
            v-model="createName"
            type="text"
            placeholder="实例名（字母/数字/-/_）"
            @keyup.enter="doCreate"
          />
          <select v-model="createPlatform">
            <option v-for="opt in platformOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
          <button class="btn btn-primary btn-sm" :disabled="!createName.trim()" @click="doCreate">
            创建
          </button>
          <button class="btn btn-secondary btn-sm" @click="cancelCreate">取消</button>
        </div>
        <div class="create-form-subrow">
          <input
            v-model="createDescription"
            type="text"
            placeholder="描述（可选）"
            @keyup.enter="doCreate"
          />
          <input
            v-model="createPort"
            type="number"
            min="1024"
            max="65535"
            placeholder="端口（可选，默认 8080 起自动分配）"
            @keyup.enter="doCreate"
          />
        </div>
        <div class="create-form-hint">
          {{ platformOptions.find((o) => o.value === createPlatform)?.hint }}
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="!showCreateForm && store.instances.length === 0" class="empty-state">
        <div class="icon">▣</div>
        <div>还没有实例</div>
        <div style="font-size: 12px; margin-top: 6px">点击「新建实例」创建第一个</div>
      </div>

      <!-- 实例表格 -->
      <table v-else-if="store.instances.length" class="table">
        <thead>
          <tr>
            <th class="col-status">状态</th>
            <th>名称</th>
            <th>平台</th>
            <th class="col-num">端口</th>
            <th>启用的适配器</th>
            <th class="col-num">数据</th>
            <th class="col-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="inst in store.instances"
            :key="inst.name"
            :class="{ 'row-active': inst.running }"
          >
            <td>
              <span class="status-dot" :class="inst.running ? 'green' : 'gray'" />
              <span class="status-label">{{ inst.running ? '运行中' : '已停止' }}</span>
            </td>
            <td class="td-name-cell">
              <div class="td-name">{{ inst.name }}</div>
              <div v-if="inst.description" class="td-desc">{{ inst.description }}</div>
            </td>
            <td>
              <span class="instance-badge">{{ platformLabel(inst.platform) }}</span>
            </td>
            <td class="td-mono">{{ inst.port }}</td>
            <td>
              <div class="adapter-tags">
                <span v-for="a in enabledAdapters(inst.adapters)" :key="a.key" class="adapter-tag">
                  {{ a.label }}
                </span>
                <span v-if="enabledAdapters(inst.adapters).length === 0" class="text-muted"
                  >无</span
                >
              </div>
            </td>
            <td class="td-mono">{{ fmtBytes(inst.disk_usage) }}</td>
            <td class="td-actions">
              <button
                class="btn btn-sm btn-success"
                :disabled="inst.running"
                @click="onSwitch(inst)"
              >
                切换
              </button>
              <button class="btn btn-sm btn-secondary" @click="onRename(inst)">重命名</button>
              <button
                class="btn btn-sm btn-danger"
                :disabled="inst.running"
                @click="onDelete(inst)"
              >
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 平台提示 -->
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">关于实例与平台</div>
      </div>
      <div class="hint-text" style="padding: 4px 0">
        <p style="margin-bottom: 10px; line-height: 1.8">
          <strong>实例</strong>是一个独立的运行单元，包含独立的 config、data 和 plugins 目录。
          每个实例绑定一个<strong>主平台</strong>，创建时可选：
        </p>
        <ul style="margin: 0 0 10px 16px; padding: 0; line-height: 1.8">
          <li>
            <strong>OneBot 11</strong> — 反向 WebSocket，对接 OneBot 11 协议端（如 LLBot / NapCat）
          </li>
          <li>
            <strong>OneBot 12</strong> — 原生反向 WebSocket，事件直通无需翻译，对接 NapCat /
            Lagrange.OneBot 等实现端
          </li>
          <li><strong>Telegram</strong> — Bot API 长轮询，创建后在系统设置中填写 Bot Token</li>
        </ul>
        <p style="line-height: 1.8">
          切换实例会重启应用以加载目标实例的配置与数据。 删除实例将清空该实例的所有文件，无法恢复。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.col-status {
  width: 100px;
}
.col-actions {
  width: 220px;
}
.td-name {
  font-family: var(--font-mono);
  font-weight: 600;
}
.td-actions {
  display: flex;
  gap: 6px;
}
.row-active {
  background: var(--accent-bg);
}
.status-label {
  font-size: 12px;
  margin-left: 6px;
  color: var(--text-secondary);
}
.instance-badge {
  display: inline-block;
  padding: 2px 10px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 10px;
  background: var(--bg-hover);
  color: var(--accent);
  border: 1px solid rgba(251, 191, 36, 0.2);
}
.row-active .instance-badge {
  background: rgba(251, 191, 36, 0.12);
}

.active-instance-card {
  padding: 8px 0;
}
.active-instance-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  background: rgba(16, 185, 129, 0.06);
  border: 1px solid rgba(16, 185, 129, 0.15);
  border-radius: var(--radius-sm);
}
.active-instance-name {
  font-weight: 700;
  font-family: var(--font-mono);
  font-size: 15px;
}
.active-instance-running {
  margin-left: auto;
  font-size: 12px;
  color: var(--success);
  font-weight: 600;
}

.create-form-inline {
  margin-bottom: 16px;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.02);
}
.create-form-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.create-form-row input,
.create-form-row select {
  padding: 6px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xs);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
}
.create-form-row input {
  flex: 1;
  min-width: 0;
}
.create-form-row input:focus,
.create-form-row select:focus {
  border-color: var(--accent);
}
.create-form-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-muted);
}
.create-form-subrow {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.create-form-subrow input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xs);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
}
.create-form-subrow input:focus {
  border-color: var(--accent);
}

.active-instance-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin-top: 12px;
  padding: 0 2px;
}
.meta-item {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}
.meta-label {
  margin-right: 6px;
  font-size: 11px;
  color: var(--text-muted);
  font-family: var(--font-sans);
}

.col-num {
  width: 90px;
}
.td-mono {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
}
.td-name-cell {
  min-width: 120px;
}
.td-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-muted);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.adapter-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.adapter-tag {
  padding: 1px 8px;
  font-size: 11px;
  line-height: 1.6;
  border-radius: 8px;
  background: var(--bg-hover);
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
  white-space: nowrap;
}
</style>
