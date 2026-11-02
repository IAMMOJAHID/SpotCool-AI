// Video mode at 1920x1080: everything fits its panel and is big enough to read.
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { dashboard, EVENTS, fixture, overflowing, playedThrough, smallText } from './support.ts';
import type { Envelope } from './support.ts';

const END = 199 * 86_400 + 19 * 3600;
let tMs = 1_900_000_000_000;
const world = (type: string, data: Record<string, unknown>, simTime = END): Envelope => ({
  v: 1,
  type,
  source: 'world',
  t_ms: (tMs += 5),
  sim_time_s: simTime,
  data,
});
const agent = (type: string, data: Record<string, unknown>): Envelope => ({ ...world(type, data), source: 'agent' });

/** The texts the real world and agent send at their longest. */
function stress(): Envelope[] {
  const reason = "'Pos7' is not a state of IDU-VANE-V (['Pos1', 'Pos2', 'Pos3', 'Pos4', 'Pos5', 'Swing'])";
  const write = (objectName: string, value: string | number | null, accepted: boolean, why: string | null) =>
    world('bms_write', {
      device: 'VRF-Gateway-MeetingRoom',
      object_id: 'multi-state-value,5',
      object_name: objectName,
      priority: 10,
      value,
      client: '192.168.137.254:61234',
      accepted,
      reason: why,
    });
  const long = 'South: the meeting room has five people and the calendar booking runs to 15:00 -> ZN-SOU-OCC-SENSOR Occupied, ZN-SOU-CLG-SP released at priority 10, and the VRF louvers aimed away from the table where the occupants complained of a draught';
  return [
    world('run_status', { run_id: 'fixture-hybrid-day', status: 'finished', plant: 'boptest-hosted', scenario: 'all_hands_day_with_a_very_long_scenario_name_for_the_overflow_check', message: null }),
    write('IDU-VANE-V', 'Pos7', false, reason),
    write('ZN-SOU-CLG-SP-WITH-A-LONG-OBJECT-NAME', 26.5, false, 'value 26.5 is off the 1.0 resolution grid of ZN-SOU-CLG-SP; refused, never clamped'),
    write('IDU-VANE-H', 'Swing', false, "write-access-denied: the lg profile exposes no horizontal louver (IDU-VANE-H)"),
    agent('explanation', { text: long }),
    agent('chat', { text: `${long} (chat)` }),
    world('complaint_cue', { zone: 'sou', text: 'The AC is blowing right on my face and my papers keep flying off the table, please do something about it now', channel: 'voice', delivered: false, detail: 'POST /voice: ConnectError: All connection attempts failed (after 3 attempts)' }),
  ];
}

async function served(page: Page, envelopes: Envelope[]) {
  await page.routeWebSocket(EVENTS, (ws) => {
    for (const envelope of envelopes) ws.send(JSON.stringify(envelope));
  });
}

for (const [name, extra, agentUrl] of [
  ['the fixture', [] as Envelope[], 'http://192.168.137.1:8002'],
  ['the fixture, with no agent configured', [] as Envelope[], undefined],
  ['the fixture with the longest real texts', stress(), 'http://192.168.137.1:8002'],
  ['the longest real texts, with no agent configured', stress(), undefined],
] as const) {
  test(`nothing is cut off: ${name}`, async ({ page }) => {
    await served(page, [...fixture(), ...extra]);
    await page.goto(dashboard('video', agentUrl));
    await playedThrough(page);
    expect(await overflowing(page)).toEqual([]);
  });

  test(`no text is under 22 px; titles, the clock and energy saved are 28 px or more: ${name}`, async ({ page }) => {
    await served(page, [...fixture(), ...extra]);
    await page.goto(dashboard('video', agentUrl));
    await playedThrough(page);
    expect(await smallText(page, 22)).toEqual([]);
    expect(await smallText(page, 28, '.panel-head h2, [data-testid=energy-saved], [data-testid=clock]')).toEqual([]);
    if (agentUrl) await expect(page.getByTestId('talk-host')).toHaveText('to 192.168.137.1:8002');
  });
}
