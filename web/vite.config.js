import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/ui/',
  server: {
    port: 5173,
    proxy: {
      '/api/ws': {
        target: 'ws://127.0.0.1:8080',
        ws: true
      },
      '/api': 'http://127.0.0.1:8080'
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    emptyOutDir: true
  }
})