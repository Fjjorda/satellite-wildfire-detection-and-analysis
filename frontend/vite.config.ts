import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react()],
    optimizeDeps: { include: ['pdfkit/js/pdfkit.standalone.js', 'fflate'] },
    server: {
      port: 5173,
      strictPort: true,
      proxy: { '/api': process.env.API_PROXY_TARGET ?? env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' },
    },
  };
});
