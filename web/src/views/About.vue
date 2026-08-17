<script setup>
</script>

<template>
  <div class="page-header">
    <h1>关于</h1>
    <p>项目介绍与鸣谢</p>
  </div>

  <div class="page-body">
    <!-- 项目简介 -->
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">项目简介</div>
      </div>
      <div class="about-section">
        <p><strong>Qingci-Bot CE</strong> 是一个基于 Python 的 QQ 机器人框架，对接 LLBot（OneBot 11 协议），支持 LLM 智能对话、Web UI 和桌面应用。</p>
        <p>项目采用 <strong>GPLv3</strong> 开源许可，代码托管于 <a href="https://github.com/Qingci-Bot/Qingci-Bot-CE" target="_blank" rel="noopener">GitHub</a>。</p>
        <div class="feature-grid">
          <div class="feature-item">
            <span class="feature-icon">✦</span>
            <div>
              <div class="feature-title">多提供商 LLM</div>
              <div class="feature-desc">基于 litellm，支持 OpenAI / DeepSeek / Ollama / SiliconFlow / Claude / Gemini / 自定义</div>
            </div>
          </div>
          <div class="feature-item">
            <span class="feature-icon">◇</span>
            <div>
              <div class="feature-title">插件系统</div>
              <div class="feature-desc">借鉴 NoneBot2 的 Matcher/Rule/Permission 设计，支持热加载与依赖声明</div>
            </div>
          </div>
          <div class="feature-item">
            <span class="feature-icon">◈</span>
            <div>
              <div class="feature-title">Web 管理端</div>
              <div class="feature-desc">Vue 3 + 原神风格暗色主题，仪表盘 / LLM 配置 / 对话调试 / 插件管理</div>
            </div>
          </div>
          <div class="feature-item">
            <span class="feature-icon">▣</span>
            <div>
              <div class="feature-title">桌面应用</div>
              <div class="feature-desc">PyWebView 套壳 + 系统托盘，开机自启，关闭窗口自动驻留后台</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 鸣谢 -->
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">鸣谢</div>
      </div>
      <div class="about-section">
        <p style="color: var(--text-secondary); margin-bottom: 18px;">
          以下项目为本项目的开发提供了重要参考与支持，在此致以诚挚感谢。
        </p>
        <div class="thanks-list">
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/luoqingciya" target="_blank" rel="noopener">Qingci-Bot</a>
            </div>
            <div class="thanks-desc">
              本项目的上游项目，由 Zhou Zhe (aka luoqingci) 原创开发。Qingci-Bot CE 基于其核心代码演进而来，感谢原项目为社区版奠定的坚实基础。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/nonebot/nonebot2" target="_blank" rel="noopener">NoneBot2</a>
            </div>
            <div class="thanks-desc">
              插件系统的 Matcher / Rule / Permission 设计借鉴了 NoneBot2 的架构思想，包括优先级调度、权限控制、命令注册器等核心概念。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/LLOneBot/LuckyLilliaBot" target="_blank" rel="noopener">LLBot (LuckyLilliaBot)</a>
            </div>
            <div class="thanks-desc">
              QQ 协议端，实现 OneBot 11 标准协议，为本项目提供与 QQ 平台的通信能力。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/nonebot/aiocqhttp" target="_blank" rel="noopener">aiocqhttp</a>
            </div>
            <div class="thanks-desc">
              OneBot 11 协议的 Python 异步实现，负责反向 WebSocket 连接、事件解析与 API 调用。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/BerriAI/litellm" target="_blank" rel="noopener">litellm</a>
            </div>
            <div class="thanks-desc">
              LLM 统一接口层，支持 100+ 模型提供商的统一调用格式，实现流式响应、Function Calling 与多模态能力。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/fastapi/fastapi" target="_blank" rel="noopener">FastAPI</a>
            </div>
            <div class="thanks-desc">
              高性能 Python Web 框架，为本项目提供 API 服务、WebSocket 支持与自动文档生成。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/tiangolo/sqlmodel" target="_blank" rel="noopener">SQLModel</a>
              &nbsp;/&nbsp;
              <a href="https://github.com/sqlalchemy/alembic" target="_blank" rel="noopener">Alembic</a>
            </div>
            <div class="thanks-desc">
              SQLModel 提供 ORM 模型定义与异步会话支持；Alembic 负责数据库 schema 迁移管理。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/vuejs/core" target="_blank" rel="noopener">Vue 3</a>
              &nbsp;/&nbsp;
              <a href="https://github.com/vitejs/vite" target="_blank" rel="noopener">Vite</a>
              &nbsp;/&nbsp;
              <a href="https://github.com/vuejs/pinia" target="_blank" rel="noopener">Pinia</a>
            </div>
            <div class="thanks-desc">
              前端技术栈：Vue 3 组件化开发、Vite 构建工具、Pinia 状态管理，共同构建 Web 管理界面。
            </div>
          </div>
          <div class="thanks-item">
            <div class="thanks-name">
              <a href="https://github.com/r0x0r/pywebview" target="_blank" rel="noopener">PyWebView</a>
              &nbsp;/&nbsp;
              <a href="https://github.com/moses-palmer/pystray" target="_blank" rel="noopener">pystray</a>
            </div>
            <div class="thanks-desc">
              PyWebView 提供桌面应用 WebView 容器；pystray 实现系统托盘图标与后台驻留能力。
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 版本信息 -->
    <div class="card fade-in">
      <div class="card-header">
        <div class="card-title">版本信息</div>
      </div>
      <div class="about-section">
        <div class="version-grid">
          <div class="version-item">
            <span class="version-label">Qingci-Bot CE</span>
            <span class="version-value">1.5.1</span>
          </div>
          <div class="version-item">
            <span class="version-label">许可证</span>
            <span class="version-value">GPL-3.0-or-later</span>
          </div>
          <div class="version-item">
            <span class="version-label">Python</span>
            <span class="version-value">3.10+</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.about-section {
  padding: 4px 0;
}

.about-section p {
  line-height: 1.8;
  margin-bottom: 10px;
  color: var(--text-secondary);
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
  margin-top: 16px;
}

@media (max-width: 640px) {
  .feature-grid {
    grid-template-columns: 1fr;
  }
}

.feature-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px;
  background: rgba(15, 23, 42, 0.4);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}

.feature-icon {
  font-size: 20px;
  color: var(--accent);
  flex-shrink: 0;
  margin-top: 1px;
}

.feature-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.feature-desc {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.6;
}

.thanks-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.thanks-item {
  padding: 14px 16px;
  background: rgba(15, 23, 42, 0.4);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}

.thanks-name {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 6px;
}

.thanks-name a {
  color: var(--blue);
  text-decoration: none;
  transition: color 0.2s;
}

.thanks-name a:hover {
  color: var(--accent);
}

.thanks-desc {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.7;
}

.version-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}

@media (max-width: 640px) {
  .version-grid {
    grid-template-columns: 1fr;
  }
}

.version-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px;
  background: rgba(15, 23, 42, 0.4);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}

.version-label {
  font-size: 12px;
  color: var(--text-muted);
}

.version-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  font-family: var(--font-mono);
}
</style>