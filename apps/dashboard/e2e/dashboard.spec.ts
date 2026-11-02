// End to end against the built app (`npm run build` first) and the mock feed playing the fixture.
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { AGENT, MOCK } from '../playwright.config.ts';

const PANELS = ['header', 'floorplan', 'cameras', 'kpis', 'energy', 'timeline', 'bacnet', 'acunit', 'agent'];
const url = (mode: 'operator' | 'video', agent = true) =>
  `/?world=${MOCK}&cameras=${MOCK}${agent ? `&agent=${AGENT}` : ''}${mode === 'video' ? '&mode=video' : ''}`;

/** Wait for the fixture's end: the run finished, final KPIs, camera frames and louvers settled. */
async function playedThrough(page: Page) {
  await expect(page.getByTestId('run-status')).toHaveText('Finished', { timeout: 20_000 });
  await expect(page.getByTestId('energy-saved')).not.toHaveText('—');
  await expect
    .poll(() => page.locator('.tile img').evaluateAll((imgs) => imgs.every((i) => (i as HTMLImageElement).naturalWidth > 0)))
    .toBe(true);
  await page.waitForTimeout(400); // the VRF louvers ease in over 200 ms
}

/** The named parts of a multipart body. */
function multipart(body: Buffer, contentType: string): Map<string, Buffer> {
  const boundary = contentType.match(/boundary=(.+)$/)?.[1] ?? '';
  const parts = new Map<string, Buffer>();
  for (const section of body.toString('latin1').split(`--${boundary}`)) {
    const name = section.match(/name="([^"]+)"/)?.[1];
    if (name) parts.set(name, Buffer.from(section.slice(section.indexOf('\r\n\r\n') + 4, -2), 'latin1'));
  }
  return parts;
}

test('every panel populates within 5 s', async ({ page }) => {
  await page.goto(url('operator'));
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
  await page.goto(url('operator'));
  await playedThrough(page);
  await expect(page).toHaveScreenshot('operator.png', { fullPage: true, scale: 'device' });
});

test('video mode matches its screenshot, without scrollbars', async ({ page }) => {
  await page.goto(url('video'));
  await playedThrough(page);
  const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  expect(size).toEqual([1920, 1080]);
  await expect(page).toHaveScreenshot('video.png', { scale: 'device' });
});
});

test('video mode shows no ground truth and no BOPTEST discomfort; stub events say so', async ({ page }) => {
  await page.goto(url('video'));
  await playedThrough(page);
  const text = await page.locator('body').innerText();
  expect(text).not.toMatch(/script/i);
  expect(text).not.toMatch(/tdis_tot|idis_tot/);
  await expect(page.locator('.tag-script')).toHaveCount(0);
  await expect(page.getByText('Stub agent', { exact: true })).toBeVisible();
  await expect(page.getByTestId('panel-agent').locator('.tag-stub').first()).toBeVisible();
  // The bottom 12% stays clear for captions.
  const bottom = await page.locator('main').evaluate((m) => m.getBoundingClientRect().bottom);
  expect(bottom).toBeLessThanOrEqual(1080 * 0.88);
});

test("operator view labels the script's counts and qualifies BOPTEST's KPIs", async ({ page }) => {
  await page.goto(url('operator'));
  await playedThrough(page);
  await expect(page.locator('.tag-script').first()).toBeVisible();
  const boptest = page.getByTestId('boptest-kpis');
  await expect(boptest).toContainText('tdis_tot');
  await expect(boptest).toContainText('idis_tot');
  await expect(page.getByTestId('boptest-caption')).toHaveText(
    "BOPTEST's schedule counts every zone as occupied all day, so its discomfort includes zones the scenario leaves empty.",
  );
  await expect(page.locator('[data-testid=complaint-marker]')).toHaveCount(2);
  await expect(page.locator('[data-status=delivered]')).toHaveCount(1);
  await expect(page.locator('[data-status=failed]')).toHaveCount(1);
  await expect(page.getByTestId('vrf-profile')).toHaveText('Mitsubishi');
});

test('the chat box posts a ComplaintText to the agent', async ({ page }) => {
  const bodies: unknown[] = [];
  await page.route(`${AGENT}/chat`, async (route) => {
    bodies.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, headers: { 'Access-Control-Allow-Origin': '*' }, json: { ok: true } });
  });
  await page.goto(url('operator'));
  await page.getByLabel('Complaint to the agent').fill('  It is too cold by the window  ');
  await page.getByLabel('Complaint to the agent').press('Enter');
  await expect(page.getByText('Sent to the agent.')).toBeVisible();
  expect(bodies).toEqual([{ text: 'It is too cold by the window', source: 'occupant' }]);
});

test('hold-to-talk works from the keyboard and posts a 16 kHz mono 16-bit WAV', async ({ page }) => {
  const sent: Array<Map<string, Buffer>> = [];
  await page.route(`${AGENT}/voice`, async (route) => {
    const request = route.request();
    sent.push(multipart(request.postDataBuffer() ?? Buffer.alloc(0), request.headers()['content-type'] ?? ''));
    await route.fulfill({ status: 200, headers: { 'Access-Control-Allow-Origin': '*' }, json: { ok: true } });
  });
  await page.goto(url('operator'));
  const talk = page.getByRole('button', { name: 'Hold to talk' });
  await talk.focus();
  await page.keyboard.down('Space');
  await expect(page.getByRole('button', { name: 'Recording…' })).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(1200);
  await page.keyboard.up('Space');
  await expect(page.getByTestId('talk-status')).toHaveText('Voice message sent to the agent.');
  expect(sent).toHaveLength(1);
  const audio = sent[0]?.get('audio') ?? Buffer.alloc(0);
  expect(audio.toString('ascii', 0, 4)).toBe('RIFF');
  expect(audio.toString('ascii', 8, 12)).toBe('WAVE');
  expect(audio.readUInt16LE(20)).toBe(1); // PCM
  expect(audio.readUInt16LE(22)).toBe(1); // mono
  expect(audio.readUInt32LE(24)).toBe(16_000);
  expect(audio.readUInt16LE(34)).toBe(16);
  expect(audio.readUInt32LE(40)).toBeGreaterThan(16_000); // over half a second of samples
  expect(JSON.parse(sent[0]?.get('meta')?.toString('utf8') ?? '{}')).toMatchObject({ source: 'occupant' });
});

test('without an agent the panel says so', async ({ page }) => {
  await page.goto(url('operator', false));
  await expect(page.getByText(/No agent configured/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hold to talk' })).toHaveCount(0);
});

test('a camera whose stream fails falls back to snapshots', async ({ page }) => {
  await page.route('**/cameras/cam-sou/mjpeg', (route) => route.abort());
  await page.goto(url('operator'));
  const tile = page.locator('[data-camera=cam-sou]');
  await expect(tile.locator('.cam-note')).toHaveText('snapshots');
  await expect(tile.locator('img')).toHaveAttribute('src', /snapshot\.jpg\?frame=\d+/);
});
