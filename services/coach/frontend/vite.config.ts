import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The coach FastAPI serves this build under /coach/ (StaticFiles mount), so assets must
// resolve against that base. The chat + stt-token APIs are same-origin (/api/*).
export default defineConfig({
  base: '/coach/',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
})
