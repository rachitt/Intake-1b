import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs separately on :8000; proxying keeps the browser on one origin so
// uploads and image requests need no CORS dance during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
