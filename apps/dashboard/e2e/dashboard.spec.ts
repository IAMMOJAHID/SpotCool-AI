// End to end against the built app (`npm run build` first) and the mock feed playing the fixture.
import { expect, test } from '@playwright/test';
import { dashboard, fixtureResults, overflowing, playedThrough } from './support.ts';

const PANELS = ['header', 'floorplan', 'cameras', 'kpis', 'energy', 'timeline', 'bacnet', 'acunit', 'agent'];

test('every panel populates within 5 s', async ({ page }) => {
  await page.goto(dashboard('operator'));
  for (const id of PANELS) {
    await expect(page.getByTestId(`panel-${id}`), id).toHaveAttribute('data-populated', 'true', { timeout: 5000 });
  }
  await expect(page.locator('.tile')).toHaveCount(6); // five zones and the live phone
  await expect(page.locator('.tile-live')).toHaveCount(1);
});

test.describe('screenshots', () => {
  // Half the device pixels: the same CSS layout, a quarter of the baseline's size.
  test.use({ deviceScaleFactor: 0.5 });

  test('operator view matches its screenshot', async ({ page }) => {
    await page.goto(dashboard('operator'));
    await playedThrough(page);
    await fixtureResults(page);
    await expect(page).toHaveScreenshot('operator.png', { fullPage: true, scale: 'device' });
  });

  test('video mode matches its screenshot, without scrollbars', async ({ page }) => {
    await page.goto(dashboard('video'));
    await playedThrough(page);
    await fixtureResults(page);
    const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
    expect(size).toEqual([1920, 1080]);
    await expect(page).toHaveScreenshot('video.png', { scale: 'device' });
  });
});

test('video mode shows no ground truth and no BOPTEST discomfort; stub events say so', async ({ page }) => {
  await page.goto(dashboard('video'));
  await playedThrough(page);
  const text = await page.locator('body').innerText();
  expect(text).not.toMatch(/script/i);
  expect(text).not.toMatch(/tdis_tot|idis_tot/);
  await expect(page.locator('.tag-script')).toHaveCount(0);
  await expect(page.getByText('Stub agent', { exact: true })).toBeVisible();
  await expect(page.getByTestId('panel-agent').locator('.tag-stub').first()).toBeVisible();
  await expect(page.getByTestId('kpi-stub')).toHaveText('Stub agent: upper bound with perfect perception');
  // The bottom 12% stays clear for captions.
  const bottom = await page.locator('main').evaluate((m) => m.getBoundingClientRect().bottom);
  expect(bottom).toBeLessThanOrEqual(1080 * 0.88);
});

test("operator view labels the script's counts and qualifies BOPTEST's KPIs", async ({ page }) => {
  await page.goto(dashboard('operator'));
  await playedThrough(page);
  await expect(page.locator('.tag-script').first()).toBeVisible();
  const boptest = page.getByTestId('boptest-kpis');
  await expect(boptest).toContainText('tdis_tot');
  await expect(boptest).toContainText('idis_tot');
  await expect(page.getByTestId('boptest-caption')).toHaveText(
    "BOPTEST's schedule counts every zone as occupied all day, so its discomfort includes zones the scenario leaves empty.",
  );
  await expect(page.getByTestId('kpi-stub')).toBeVisible();
  await expect(page.locator('[data-testid=complaint-marker]')).toHaveCount(2);
  await expect(page.locator('[data-status=delivered]')).toHaveCount(1);
  await expect(page.locator('[data-status=failed]')).toHaveCount(1);
  await expect(page.getByTestId('vrf-profile')).toHaveText('Mitsubishi');
});

test('without an agent the panel says so', async ({ page }) => {
  await page.goto(dashboard('operator'));
  await expect(page.getByText(/No agent configured/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hold to talk' })).toHaveCount(0);
});

test('an agent off this network is refused and reported, not used', async ({ page }) => {
  await page.goto(`${dashboard('operator')}&agent=https://evil.example`);
  await expect(page.getByText(/Agent not configured: "https:\/\/evil.example" is not on this machine/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hold to talk' })).toHaveCount(0);
});

test('a camera with no stream and no snapshot says "no signal", inside its tile', async ({ page }) => {
  await page.route('**/cameras/cam-nor/*', (route) => route.abort());
  await page.goto(dashboard('video'));
  const tile = page.locator('[data-camera=cam-nor]');
  await expect(tile.locator('.cam-lost')).toHaveText('no signal');
  await expect(tile.locator('img')).toBeHidden();
  expect(await overflowing(page)).toEqual([]);
});

test('a camera whose stream fails falls back to snapshots', async ({ page }) => {
  await page.route('**/cameras/cam-sou/mjpeg', (route) => route.abort());
  await page.goto(dashboard('operator'));
  const tile = page.locator('[data-camera=cam-sou]');
  await expect(tile.locator('.cam-note')).toHaveText('snapshots');
  await expect(tile.locator('img')).toHaveAttribute('src', /snapshot\.jpg\?frame=\d+/);
});
