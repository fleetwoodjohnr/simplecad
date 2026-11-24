import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'tests/e2e',
  // The first start on a fresh runner is slow for reasons outside the app; see warm.ts.
  globalSetup: './tests/e2e/warm.ts',
  timeout: 120_000,
  workers: 1,
  // CI runners are slower and noisier than a desktop: a test that passes on retry is reported as flaky, not hidden.
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  use: { trace: 'retain-on-failure' }
})
