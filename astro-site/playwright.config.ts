import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 90_000,
  retries: process.env.CI ? 1 : 0,
  workers: 2,
  use: {
    baseURL: 'http://127.0.0.1:8874/helia-profiler/',
    browserName: 'chromium',
    headless: true,
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npx helia-ui-serve-dist --port 8874 --base /helia-profiler --dist dist',
    url: 'http://127.0.0.1:8874/helia-profiler/',
    reuseExistingServer: false,
  },
});
