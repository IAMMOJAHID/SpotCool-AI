// The event feed: reconnects, malformed events and a panel that fails to draw.
import { expect, test } from '@playwright/test';
import { dashboard, EVENTS, fixture } from './support.ts';
import type { Envelope } from './support.ts';

const events = fixture();
const count = (type: string) => events.filter((e) => e.type === type).length;
const AGENT_ITEMS = count('decision') + count('explanation') + count('chat');

test('a reconnect that replays the run so far shows no row twice', async ({ page }) => {
  let connections = 0;
  await page.routeWebSocket(EVENTS, (ws) => {
    connections += 1;
    if (connections === 1) {
      for (const envelope of events.slice(0, Math.floor(events.length / 2))) ws.send(JSON.stringify(envelope));
      setTimeout(() => void ws.close(), 300); // the world goes away mid-run
    } else {
      for (const envelope of events) ws.send(JSON.stringify(envelope)); // and sends the run so far
    }
  });
  await page.goto(dashboard('operator'));
  await expect(page.getByTestId('run-status')).toHaveText('Finished', { timeout: 15_000 });
  expect(connections).toBe(2);
  await expect(page.getByTestId('panel-bacnet').locator('tbody tr')).toHaveCount(count('bms_write'));
  await expect(page.getByTestId('panel-agent').locator('.feed li')).toHaveCount(AGENT_ITEMS);
  await expect(page.locator('[data-testid=complaint-marker]')).toHaveCount(2);
});

test('a malformed world event is dropped and counted, and every panel carries on', async ({ page }) => {
  const at = events.findIndex((e) => e.type === 'tick') + 1;
  const bad: Envelope[] = [
    { ...events[1], data: { ...events[1]?.data, zones: null } } as Envelope, // plant_measurements
    { v: 1, type: 'tick', source: 'world', t_ms: 1, sim_time_s: 1, data: { index: 9, clock: '07:15', day_of_year: 200 } },
  ];
  expect(events[1]?.type).toBe('plant_measurements');
  await page.routeWebSocket(EVENTS, (ws) => {
    for (const envelope of [...events.slice(0, at), ...bad, ...events.slice(at)]) ws.send(JSON.stringify(envelope));
  });
  await page.goto(dashboard('operator'));
  await expect(page.getByTestId('run-status')).toHaveText('Finished', { timeout: 15_000 });
  await expect(page.getByTestId('panel-errors')).toContainText('2 invalid messages dropped');
  for (const id of ['floorplan', 'energy', 'timeline', 'kpis', 'bacnet', 'agent']) {
    await expect(page.getByTestId(`panel-${id}`), id).toHaveAttribute('data-populated', 'true');
  }
  await expect(page.getByTestId('panel-error')).toHaveCount(0);
});

test('a panel that fails to draw says so, and the others keep working', async ({ page }) => {
  // A KPI the browser cannot format: the Results panel throws while it draws.
  await page.addInitScript(() => {
    const toFixed: (this: number, digits?: number) => string = Reflect.get(Number.prototype, 'toFixed');
    Number.prototype.toFixed = function (this: number, digits?: number) {
      if (this.valueOf() === 12.3456) throw new Error('cannot format 12.3456');
      return toFixed.call(this, digits);
    };
  });
  const poison: Envelope = {
    v: 1,
    type: 'kpi',
    source: 'world',
    t_ms: 2_000_000_000_000,
    sim_time_s: 199 * 86_400 + 19 * 3600,
    data: { run: 'spotcool', boptest: {}, comfort_where_people_kh: 1, energy_saved_pct: 12.3456, provisional: false },
  };
  await page.routeWebSocket(EVENTS, (ws) => {
    for (const envelope of [...events, poison]) ws.send(JSON.stringify(envelope));
  });
  await page.goto(dashboard('operator'));
  await expect(page.getByTestId('panel-error')).toHaveText('This panel failed to draw: cannot format 12.3456');
  await expect(page.getByTestId('panel-kpis')).toHaveAttribute('data-populated', 'false');
  for (const id of ['floorplan', 'energy', 'timeline', 'bacnet', 'agent', 'acunit']) {
    await expect(page.getByTestId(`panel-${id}`), id).toHaveAttribute('data-populated', 'true');
  }
  await expect(page.getByTestId('run-status')).toHaveText('Finished');
});
