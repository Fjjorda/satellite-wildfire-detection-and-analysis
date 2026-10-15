import { defineConfig, devices } from '@playwright/test';
import { resolve } from 'node:path';

const python = process.env.FIREWATCH_PYTHON ?? (process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python');
const preview = process.env.FIREWATCH_PREVIEW === '1';

export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1,
  timeout: 90_000, expect: { timeout: 20_000 },
  use: { baseURL: 'http://127.0.0.1:5174', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: `"${resolve(python)}" -B ../tests/web_fixture_server.py`, url: 'http://127.0.0.1:8001/api/health', env: { FIREWATCH_ORIGINS: 'http://127.0.0.1:5174' }, reuseExistingServer: !process.env.CI, timeout: 60_000 },
    { command: preview ? 'npm run preview -- --port 5174' : 'npm run dev -- --port 5174', url: 'http://127.0.0.1:5174', env: { API_PROXY_TARGET: 'http://127.0.0.1:8001' }, reuseExistingServer: !process.env.CI },
  ],
});
