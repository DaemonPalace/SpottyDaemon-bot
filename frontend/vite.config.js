import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev-only: production always has the supervisor serve frontend/dist
    // directly (same origin, no proxy needed). This just lets `npm run
    // dev` talk to a supervisor running on its default port.
    proxy: {
      '/api': 'http://127.0.0.1:8080',
    },
  },
})
