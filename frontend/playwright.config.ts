import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests-e2e',
  fullyParallel: true,
  // Cold-cache Haley tables + dep graph can take 30-60s against live prod;
  // a parallel worker that grabs the cold slot while another warms the cache
  // occasionally times out at the backend's first call. Two retries absorb
  // that jitter without masking real regressions.
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['html'], ['github']] : 'list',
  // Global per-test timeout raised to cover Haley's cold cache combined table.
  timeout: 60_000,
  expect: {
    timeout: 20_000,
  },
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
