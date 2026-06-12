// @ts-check
const { defineConfig, devices } = require('@playwright/test');

/**
 * Playwright configuration for the RAMP documentation viewer E2E tests.
 *
 * IMPORTANT: index.html uses fetch() to build its search index, which fails
 * under the file:// protocol (CORS). So we serve the parent folder
 * (RampMotorMiddlewareDoc) over HTTP and point the tests at it.
 *
 * The `webServer` block below auto-starts a static file server before the
 * tests run, and shuts it down afterwards.
 */
module.exports = defineConfig({
  testDir: './',
  testMatch: '**/*.spec.js',

  // Each test gets up to 60s (indexing fetches ~30 pages on startup).
  timeout: 60 * 1000,
  expect: {
    timeout: 10 * 1000
  },

  fullyParallel: false, // keep deterministic; the app is a single page
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,

  reporter: [
    ['list'],
    ['html', { open: 'never' }]
  ],

  use: {
    baseURL: 'http://localhost:8080',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Video disabled: it requires the ffmpeg binary, which cannot be
    // downloaded behind the corporate proxy (self-signed cert).
    video: 'off'
  },

  projects: [
    {
      name: 'chromium',
      // Use the locally installed Microsoft Edge (Chromium-based) instead of
      // downloading Playwright's bundled Chromium. This avoids corporate-proxy
      // self-signed-certificate download failures.
      use: { ...devices['Desktop Edge'], channel: 'msedge' }
    }
  ],

  // Serve the documentation folder (one level up from /tests).
  webServer: {
    command: 'npx http-server .. -p 8080 -c-1 --silent',
    url: 'http://localhost:8080/index.html',
    reuseExistingServer: !process.env.CI,
    timeout: 30 * 1000
  }
});
