import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // Плагины собирают компоненты Vue и стили Tailwind в статические файлы.
  plugins: [vue(), tailwindcss()],
  server: {
    // В режиме разработки запросы к API идут на локальный сервер приложения.
    proxy: {
      '/api': {
        target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
