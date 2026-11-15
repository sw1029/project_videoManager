import { defineConfig } from 'vite';
export default defineConfig({ server: { port: 5173, proxy: {
  '/api': { target: process.env.FRAME_API_URL || 'http://localhost:8000', changeOrigin: true },
  '/health': { target: process.env.FRAME_API_URL || 'http://localhost:8000', changeOrigin: true }
}} });
