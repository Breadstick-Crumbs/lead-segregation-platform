import { defineConfig, devices } from '@playwright/test'

// End-to-end tests against a running stack (frontend/e2e/stack.sh starts one on :8098).
export default defineConfig({
  testDir: './e2e',
  // One worker: the tests share the seeded data and the stack's sign-in rate limits.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8098',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
