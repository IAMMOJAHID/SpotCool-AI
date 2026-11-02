import { defineConfig } from '@playwright/test';

/** The mock feed (events and cameras) and the built app, each on a free port of its own. */
export const MOCK = '127.0.0.1:41731';
export const APP = 'http://127.0.0.1:41730';

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  // Baselines are Edge on Windows; another platform gets its own (`--update-snapshots`).
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}-{platform}{ext}',
  expect: { toHaveScreenshot: { maxDiffPixels: 200, animations: 'disabled' } },
  use: {
    channel: 'msedge', // the installed Microsoft Edge; no Playwright browser download
    baseURL: APP,
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    permissions: ['microphone'],
    launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  },
  webServer: [
    {
      // 7200 sim seconds a second: the fixture's 12-hour day plays in 6 s.
      command: `node scripts/mock-feed.mjs --port ${MOCK.split(':')[1]} --speed 7200`,
      url: `http://${MOCK}/healthz`,
      reuseExistingServer: false,
    },
    {
      command: 'npx vite preview --host 127.0.0.1 --port 41730 --strictPort',
      url: APP,
      reuseExistingServer: false,
    },
  ],
});
