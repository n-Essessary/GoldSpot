import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Проксируем /api/* → FastAPI на :8000
      // Так frontend работает на :5173 без CORS-проблем в dev
      '/api': {
        target: 'scintillating-flexibility-production-809a.up.railway.app',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
