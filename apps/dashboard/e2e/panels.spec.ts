// The live quad take's views: `?panels=` and `?masks=off` in a 960x540 OBS browser source, and an
// RC run's figures kept off the screen.
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { dashboard, EVENTS, fixture, overflowing, smallText } from './support.ts';

test.use({ viewport: { width: 960, height: 540 } });

/** The fixture at once; with `plant`, its run says that plant. */
async function served(page: Page, plant?: string) {
  const envelopes = fixture().map((e) => (plant && e.type === 'run_status' ? { ...e, data: { ...e.data, plant } } : e));
  await page.routeWebSocket(EVENTS, (ws) => {
    for (const envelope of envelopes) ws.send(JSON.stringify(envelope));
  });
}

async function framesLoaded(page: Page) {
  await expect(page.locator('.tile').first()).toBeVisible();
  await expect
    .poll(() => page.locator('.tile img').evaluateAll((imgs) => imgs.every((i) => (i as HTMLImageElement).naturalWidth > 0)))
    .toBe(true);
}

const panelIds = (page: Page) => page.locator('.panel').evaluateAll((els) => els.map((el) => el.getAttribute('data-testid')));

test('panels=cameras: the camera tiles alone fill the window, large, with their counts and boxes', async ({ page }) => {
  await served(page);
  await page.goto(`${dashboard('video')}&panels=cameras`);
  await framesLoaded(page);
  expect(await panelIds(page)).toEqual(['panel-cameras']);
  await expect(page.locator('.tile')).toHaveCount(6); // five zones and the live phone
  await expect(page.getByTestId('camera-count').first()).toBeVisible();
  await expect(page.locator('.tile svg.masks').first()).toBeAttached();
  const box = await page.getByTestId('panel-cameras').boundingBox();
  expect(box && box.width > 940 && box.height > 520).toBe(true);
  const tile = await page.locator('.tile').first().boundingBox();
  expect(tile && tile.width > 280).toBe(true);
  const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  expect(size).toEqual([960, 540]);
  expect(await overflowing(page)).toEqual([]);
  expect(await smallText(page, 22)).toEqual([]);
});

test('masks=off: the raw feeds, without boxes or counts', async ({ page }) => {
  await served(page);
  await page.goto(`${dashboard('video')}&panels=cameras&masks=off`);
  await framesLoaded(page);
  await expect(page.locator('.tile svg.masks')).toHaveCount(0);
  await expect(page.getByTestId('camera-count')).toHaveCount(0);
  expect(await overflowing(page)).toEqual([]);
});

test('panels=floorplan,energy,agent: those three fill the window and nothing is cut off', async ({ page }) => {
  await served(page);
  await page.goto(`${dashboard('video')}&panels=floorplan,energy,agent`);
  for (const id of ['floorplan', 'energy', 'agent']) {
    await expect(page.getByTestId(`panel-${id}`)).toHaveAttribute('data-populated', 'true');
  }
  expect(await panelIds(page)).toEqual(['panel-floorplan', 'panel-energy', 'panel-agent']);
  const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  expect(size).toEqual([960, 540]);
  expect(await overflowing(page)).toEqual([]);
  // The floor plan's own labels scale with its drawing, smaller here than in the 1920x1080 frame.
  expect(await smallText(page, 22, 'body *:not(svg, svg *)')).toEqual([]);
});

test.describe('an RC run', () => {
  test.use({ viewport: { width: 1920, height: 1080 } });

  for (const mode of ['video', 'operator'] as const) {
    test(`shows no saving and no kWh, only the RC note and the power: ${mode}`, async ({ page }) => {
      await served(page, 'rc');
      await page.goto(dashboard(mode));
      await expect(page.getByTestId('run-status')).toHaveText('Finished');
      await expect(page.getByTestId('kpi-rc')).toHaveText('RC test plant: a model for testing, no saving is quoted');
      await expect(page.getByTestId('energy-rc')).toBeVisible();
      await expect(page.getByTestId('energy-saved')).toHaveCount(0);
      const text = await page.locator('body').innerText();
      expect(text).not.toMatch(/kWh|%/);
      await expect(page.locator('.readout .big').first()).toBeVisible(); // the kW stay
      if (mode === 'video') expect(await overflowing(page)).toEqual([]);
    });
  }

  test('a BOPTEST run still shows its saving', async ({ page }) => {
    await served(page);
    await page.goto(dashboard('video'));
    await expect(page.getByTestId('energy-saved')).toHaveText('20.6 %');
    await expect(page.getByTestId('kpi-rc')).toHaveCount(0);
  });
});

// One pixel, as the agent's /privacy/<id>.jpg stand-in: any image the browser can decode.
const RENDER = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

test("privacy=on: the tiles show the agent's Muse Image renders with SAM's boxes, never a raw feed", async ({ page }) => {
  const agent = 'http://127.0.0.1:41799';
  await page.route(`${agent}/privacy/*`, (route) =>
    route.request().url().includes('/privacy/cam-sou.jpg?')
      ? route.fulfill({ status: 200, contentType: 'image/png', body: RENDER, headers: { 'cache-control': 'no-store' } })
      : route.fulfill({ status: 404, body: '' }),
  );
  const feeds: string[] = [];
  page.on('request', (request) => {
    if (/\/cameras\/[^/]+\/(mjpeg|snapshot)/.test(request.url())) feeds.push(request.url());
  });
  await served(page);
  await page.goto(`${dashboard('video', agent)}&panels=cameras&privacy=on`);
  const sou = page.locator('[data-camera=cam-sou]');
  await expect(sou.locator('img')).toBeVisible();
  await expect(sou.locator('img')).toHaveAttribute('src', `${agent}/privacy/cam-sou.jpg?n=0`);
  await expect(sou.locator('.cam-note')).toHaveText('Muse Image render');
  await expect(sou.locator('.cam-age')).toHaveText('up to ~180 s old');
  await expect(page.locator('[data-camera=cam-nor] .cam-lost')).toHaveText('rendering…');
  await expect(page.locator('.tile svg.masks').first()).toBeAttached();
  await expect(page.getByTestId('camera-count').first()).toBeVisible();
  expect(feeds).toEqual([]);
  expect(await overflowing(page)).toEqual([]);
});

test('privacy=on: a tile takes a new render every 10 s while events keep flowing', async ({ page }) => {
  const agent = 'http://127.0.0.1:41799';
  await page.route(`${agent}/privacy/*`, (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: RENDER, headers: { 'cache-control': 'no-store' } }),
  );
  const envelopes = fixture();
  const perception = envelopes.find((e) => e.type === 'perception');
  let feed: { send: (message: string) => void } | undefined;
  await page.clock.install();
  await page.routeWebSocket(EVENTS, (ws) => {
    feed = ws;
    for (const envelope of envelopes) ws.send(JSON.stringify(envelope));
  });
  await page.goto(`${dashboard('video', agent)}&panels=cameras&privacy=on`);
  const image = page.locator('[data-camera=cam-sou] img');
  await expect(image).toHaveAttribute('src', `${agent}/privacy/cam-sou.jpg?n=0`);
  // An event batch every 2 s: a live run's pace. Each one renders the App again.
  for (let second = 2; second <= 12; second += 2) {
    feed?.send(JSON.stringify({ ...perception, t_ms: (perception?.t_ms ?? 0) + second * 1000 }));
    await page.clock.runFor(2000);
  }
  await expect(image).toHaveAttribute('src', `${agent}/privacy/cam-sou.jpg?n=1`);
});
