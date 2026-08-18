<script setup>
import { useRouter } from 'vue-router';

const router = useRouter();

function goHome() {
  router.push('/');
}
</script>

<template>
  <div class="page-header">
    <h1>404</h1>
    <p>页面不存在</p>
  </div>

  <div class="page-body">
    <div class="card fade-in notfound-card">
      <div class="nf-icon">✦</div>
      <div class="nf-title">404 页面不存在</div>
      <div class="nf-desc">你访问的页面不存在或已被移除</div>
      <button class="btn btn-primary" @click="goHome"><span>◈</span> 返回首页</button>
    </div>
  </div>
</template>

<style scoped>
.notfound-card {
  text-align: center;
  padding: 56px 24px;
}

.nf-icon {
  font-size: 36px;
  color: var(--accent);
  margin-bottom: 14px;
}

.nf-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.nf-desc {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 22px;
}
</style>
