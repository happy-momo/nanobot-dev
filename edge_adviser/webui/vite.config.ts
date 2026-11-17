import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base '/dist/' so the built SPA is served by FastAPI at /dist (the SPA root).
// In dev, proxy API + SSE to the local backend so `npm run dev` works out of the box.
export default defineConfig({
  base: '/dist/',
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/stream': { target: 'http://127.0.0.1:8000', changeOrigin: true, ws: false },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
})