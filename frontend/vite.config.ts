/// <reference types="vitest/config" />

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  // Keep browser-safe VITE_* settings beside the backend settings in the repository root.
  envDir: '..',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 本地前端只访问同源 /api，后端地址可通过环境变量覆盖。
      '/api': {
        target: process.env.MOOTCOURT_API_PROXY_TARGET ?? 'http://127.0.0.1:8011',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
