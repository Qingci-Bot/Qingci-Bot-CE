<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { request } from '../api/request';
import { useAppStore } from '../stores/app';

const router = useRouter();
const store = useAppStore();
const apiKey = ref('');
const loading = ref(false);
const errorMsg = ref('');

async function doLogin() {
  if (!apiKey.value.trim()) {
    errorMsg.value = '请输入 API Key';
    return;
  }
  loading.value = true;
  errorMsg.value = '';
  try {
    // 登录接口本身免鉴权；401 由本页呈现错误，不走统一跳转（已在登录页）
    await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey.value.trim() }),
      skipAuthRedirect: true,
    });
    store.setApiKey(apiKey.value.trim());
    router.push('/');
  } catch (e) {
    errorMsg.value = `登录失败：${e.message}`;
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-wrap fade-in">
    <div class="card login-card">
      <div class="login-logo">
        <div class="title">Qingci-Bot CE</div>
        <span class="subtitle">Multi-Platform Bot Framework</span>
      </div>
      <form @submit.prevent="doLogin">
        <div class="form-group">
          <label>API Key</label>
          <input
            v-model="apiKey"
            type="password"
            placeholder="请输入服务端配置的 API Key"
            autofocus
          />
        </div>
        <button
          class="btn btn-primary btn-lg"
          type="submit"
          style="width: 100%; margin-top: 16px"
          :disabled="loading"
        >
          <span v-if="loading" class="spin" style="display: inline-block">↻</span>
          {{ loading ? '登录中' : '登 录' }}
        </button>
      </form>
      <div v-if="errorMsg" class="status-bar error">
        {{ errorMsg }}
      </div>
      <div class="hint-text" style="margin-top: 14px">
        服务端已启用 API 鉴权，请输入 config.yaml 中配置的 api_key 登录。
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.login-card {
  width: 380px;
  max-width: calc(100vw - 40px);
  padding: 32px 28px;
}

.login-logo {
  text-align: center;
  margin-bottom: 26px;
}

.login-logo .title {
  font-size: 26px;
  font-weight: 800;
  background: linear-gradient(135deg, var(--accent-light), var(--blue));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 1px;
}

.login-logo .subtitle {
  display: block;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 2px;
  text-transform: uppercase;
  margin-top: 4px;
}
</style>
