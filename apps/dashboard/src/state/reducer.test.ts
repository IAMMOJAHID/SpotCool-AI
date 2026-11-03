import { describe, expect, it } from 'vitest';
import type { Envelope, EventType } from '../wire';
import { EVENT_TYPES, MAX_MESSAGE_BYTES, parseEnvelope } from '../wire';
import type { DashboardState } from './reducer';
import { applyEvent, initialState, insertBy, MAX_ROWS, reducer, upsertByT } from './reducer';

const AGENT: ReadonlySet<EventType> = new Set(['perception', 'decision', 'explanation', 'chat']);
let clock = 1000;

function env(type: EventType, data: Record<string, unknown>, opts: { t?: number | null; tMs?: number } = {}): Envelope {
  clock += 10;
  return {
    v: 1,
    type,
    source: AGENT.has(type) ? 'agent' : 'world',
    t_ms: opts.tMs ?? clock,
    sim_time_s: opts.t === undefined ? 100 : opts.t,
    data,
  };
}

const fold = (...events: Envelope[]): DashboardState => reducer(initialState, { kind: 'events', envelopes: events, dropped: 0 });

const zone = (z: string, temp = 24) => ({ zone: z, temp_c: temp, cooling_sp_c: 24, heating_sp_c: 20, co2_ppm: 400, airflow_lps: 100 });
const measurement = (run: string, kw: number, kwh: number) => ({ run, zones: [zone('sou', 26)], power_kw: { 'CH-PWR': kw }, total_power_kw: kw, energy_kwh: kwh, outdoor_temp_c: 30 });
const status = (run_id: string, s: string) => ({ run_id, status: s, plant: 'rc', scenario: 'hybrid_tuesday', message: null });
const cue = (delivered: boolean, detail: string | null) => ({ zone: 'sou', text: "It's freezing in this meeting room.", channel: 'voice', delivered, detail });
const vrf = (angle: number) => ({ profile: 'mitsubishi', power_on: true, mode: 'Cool', setpoint_c: 24, fan: 'Auto', vane_v_pos: 'Pos3', vane_h_pos: 'Pos3', vane_v_angle_deg: angle, vane_h_angle_deg: 0, room_temp_c: 25, power_kw: 1 });

/** One envelope of each type, as the world and the stub agent publish them. */
const ONE_OF_EACH: Record<EventType, Envelope> = {
  tick: env('tick', { index: 0, step_s: 900, clock: '07:00', day_of_year: 200 }, { t: 25200 }),
  run_status: env('run_status', status('r1', 'running'), { t: 25200 }),
  scenario_state: env('scenario_state', { zones: [{ zone: 'wes', scene: 'few', people: 1, door_open: true, clothing: null }], active_events: ['door_open:wes'] }),
  plant_measurements: env('plant_measurements', measurement('spotcool', 5, 1)),
  bms_write: env('bms_write', { device: 'SpotCool-Office-BMS', object_id: 'analog-value,202', object_name: 'ZN-SOU-CLG-SP', priority: 10, value: null, client: '127.0.0.1:5', accepted: true, reason: null }),
  kpi: env('kpi', { run: 'spotcool', boptest: {}, comfort_where_people_kh: 0.5, energy_saved_pct: 12, provisional: true }),
  vrf_state: env('vrf_state', vrf(-30)),
  camera_scene: env('camera_scene', { camera_id: 'cam-sou', zone: 'sou', scene: 'meeting' }),
  calendar: env('calendar', { room: 'meeting-south', items: [] }),
  complaint_cue: env('complaint_cue', cue(false, null)),
  error: env('error', { where: 'cameras', message: 'boom', fatal: false }),
  metrics: env('metrics', { name: 'plant_step_latency', value: 0.01, unit: 's', labels: { run: 'spotcool' } }),
  perception: env('perception', { stub: true, people: { sou: 5, nowhere: 3 } }),
  decision: env('decision', { stub: true, zone: 'sou', point: 'ZN-SOU-CLG-SP', value: 28, priority: 10, reason: 'the zone is empty' }),
  explanation: env('explanation', { stub: true, text: 'South: the zone is empty' }),
  chat: env('chat', { text: 'On it.' }),
};

describe('reducer', () => {
  it('handles every event type the contracts define', () => {
    expect(Object.keys(ONE_OF_EACH).sort()).toEqual([...EVENT_TYPES].sort());
    for (const type of EVENT_TYPES) {
      const before = initialState;
      const after = applyEvent(before, ONE_OF_EACH[type]);
      expect(after, type).not.toBe(before);
    }
  });

  it('folds one of each into the panels', () => {
    const s = fold(...Object.values(ONE_OF_EACH));
    expect(s.simNow).toBe(25200 + 900);
    expect(s.run?.id).toBe('r1');
    expect(s.script?.zones[0]?.people).toBe(1);
    expect(s.doors.wes).toEqual([{ t: 100, open: true }]);
    expect(s.latest.spotcool?.totalKw).toBe(5);
    expect(s.bacnet).toHaveLength(1);
    expect(s.kpi.spotcool?.data.energy_saved_pct).toBe(12);
    expect(s.vrf?.data.vane_v_angle_deg).toBe(-30);
    expect(s.scenes['cam-sou']?.scene).toBe('meeting');
    expect(s.calendar['meeting-south']?.items).toEqual([]);
    expect(s.complaints[0]?.status).toBe('scheduled');
    expect(s.errors[0]?.data.message).toBe('boom');
    expect(Object.keys(s.metrics)).toEqual(['plant_step_latency run=spotcool']);
    expect(Object.keys(s.people)).toEqual(['sou']); // `nowhere` is not a zone
    expect(s.people.sou).toMatchObject({ people: 5, stub: true });
    expect(s.agent.map((a) => a.type)).toEqual(['decision', 'explanation', 'chat']);
    expect(s.agent.map((a) => a.stub)).toEqual([true, true, false]);
    expect(s.stubAgent).toBe(true);
  });

  it('treats a tick as the close of its step and never moves the clock back', () => {
    const s = fold(
      env('tick', { index: 1, step_s: 900, clock: '07:15', day_of_year: 200 }, { t: 26100 }),
      env('tick', { index: 0, step_s: 900, clock: '07:00', day_of_year: 200 }, { t: 25200 }),
    );
    expect(s.simNow).toBe(27000);
  });

  it('keeps lists in t_ms order when events arrive out of order', () => {
    const write = (name: string, tMs: number) =>
      env('bms_write', { device: 'd', object_id: 'o', object_name: name, priority: 10, value: 1, client: 'c', accepted: true, reason: null }, { tMs });
    const s = fold(write('c', 30), write('a', 10), write('b', 20), write('b2', 20));
    expect(s.bacnet.map((r) => r.data.object_name)).toEqual(['a', 'b', 'b2', 'c']);
    const agent = fold(env('explanation', { text: 'late' }, { tMs: 50 }), env('explanation', { text: 'early' }, { tMs: 5 }));
    expect(agent.agent.map((a) => a.data.text)).toEqual(['early', 'late']);
  });

  it('ignores a VRF state or a perception older (t_ms) than the one it shows', () => {
    const s = fold(env('vrf_state', vrf(-45), { tMs: 200 }), env('vrf_state', vrf(-15), { tMs: 100 }));
    expect(s.vrf?.data.vane_v_angle_deg).toBe(-45);
    const p = fold(env('perception', { people: { cor: 4 } }, { tMs: 200 }), env('perception', { people: { cor: 1 } }, { tMs: 100 }));
    expect(p.people.cor?.people).toBe(4);
  });

  it('keeps power in sim-time order, replacing a repeated stamp, and the latest zones', () => {
    const s = fold(
      env('plant_measurements', measurement('baseline', 9, 3), { t: 2700 }),
      env('plant_measurements', measurement('baseline', 7, 1), { t: 900 }),
      env('plant_measurements', measurement('baseline', 8, 2), { t: 1800 }),
      env('plant_measurements', measurement('baseline', 8.5, 2.1), { t: 1800 }),
    );
    expect(s.power.baseline).toEqual([
      { t: 900, kw: 7, kwh: 1 },
      { t: 1800, kw: 8.5, kwh: 2.1 },
      { t: 2700, kw: 9, kwh: 3 },
    ]);
    expect(s.latest.baseline?.t).toBe(2700);
  });

  it('shows a complaint as scheduled, then delivered or failed', () => {
    const scheduled = fold(env('complaint_cue', cue(false, null), { t: 500 }));
    expect(scheduled.complaints.map((c) => c.status)).toEqual(['scheduled']);
    const delivered = reducer(scheduled, { kind: 'events', envelopes: [env('complaint_cue', cue(true, null), { t: 500 })], dropped: 0 });
    expect(delivered.complaints).toHaveLength(1);
    expect(delivered.complaints[0]?.status).toBe('delivered');
    const failed = fold(env('complaint_cue', cue(false, null), { t: 500 }), env('complaint_cue', cue(false, 'ConnectError'), { t: 500 }));
    expect(failed.complaints[0]).toMatchObject({ status: 'failed', detail: 'ConnectError' });
  });

  it('never sends a complaint back to scheduled when its result arrives first', () => {
    const s = fold(env('complaint_cue', cue(true, null), { t: 500, tMs: 20 }), env('complaint_cue', cue(false, null), { t: 500, tMs: 10 }));
    expect(s.complaints.map((c) => c.status)).toEqual(['delivered']);
  });

  it('keeps a final KPI over a later provisional one', () => {
    const kpi = (provisional: boolean, saved: number) => ({ run: 'spotcool', boptest: provisional ? {} : { ener_tot: 0.07 }, comfort_where_people_kh: 1, energy_saved_pct: saved, provisional });
    const s = fold(env('kpi', kpi(true, 50), { t: 100 }), env('kpi', kpi(false, 17), { t: 200 }), env('kpi', kpi(true, 40), { t: 300 }));
    expect(s.kpi.spotcool?.data).toMatchObject({ provisional: false, energy_saved_pct: 17 });
    const older = fold(env('kpi', kpi(true, 30), { t: 300 }), env('kpi', kpi(true, 60), { t: 100 }));
    expect(older.kpi.spotcool?.data.energy_saved_pct).toBe(30);
  });

  it('never reopens a run that ended, and clears the screen for a new run', () => {
    const ended = fold(env('run_status', status('r1', 'finished'), { t: 900 }), env('run_status', status('r1', 'running'), { t: 0 }));
    expect(ended.run?.status).toBe('finished');
    const next = fold(
      env('run_status', status('r1', 'running')),
      env('bms_write', ONE_OF_EACH.bms_write.data),
      env('run_status', status('r2', 'running')),
    );
    expect(next.run?.id).toBe('r2');
    expect(next.bacnet).toEqual([]);
  });

  it('keeps the screen while reconnecting, and clears it when the socket is live again', () => {
    const running = fold(env('run_status', status('r1', 'running')), env('bms_write', ONE_OF_EACH.bms_write.data));
    const reconnecting = reducer(running, { kind: 'connection', connection: 'reconnecting', attempt: 1 });
    expect(reconnecting.bacnet).toHaveLength(1); // the last state stays on screen
    const live = reducer(reconnecting, { kind: 'connection', connection: 'live', attempt: 0 });
    expect(live.run).toBeNull();
    expect(live.bacnet).toEqual([]);
    expect(live.connection).toBe('live');
    expect(live.epoch).toBe(reconnecting.epoch + 1);
  });

  it('shows no row twice when a reconnect replays the run so far', () => {
    const write = (name: string) =>
      env('bms_write', { device: 'd', object_id: 'o', object_name: name, priority: 10, value: 1, client: 'c', accepted: true, reason: null });
    const run = [env('run_status', status('r1', 'running')), write('a'), env('explanation', { text: 'x' }), write('b')];
    const feed = (state: DashboardState, envelopes: Envelope[]) => reducer(state, { kind: 'events', envelopes, dropped: 0 });
    const first = feed(reducer(initialState, { kind: 'connection', connection: 'live', attempt: 0 }), run.slice(0, 3));
    const dropped = reducer(first, { kind: 'connection', connection: 'reconnecting', attempt: 1 });
    // The world sends each new viewer the run so far, then what follows.
    const again = feed(reducer(dropped, { kind: 'connection', connection: 'live', attempt: 0 }), run);
    expect(again.bacnet.map((r) => r.data.object_name)).toEqual(['a', 'b']);
    expect(again.agent).toHaveLength(1);
    expect(new Set(again.bacnet.map((r) => r.id)).size).toBe(2); // stable, unique keys
  });

  it('keeps the masks of each camera, dropping malformed polygons', () => {
    const square: number[][] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    const s = fold(env('perception', { masks: { 'cam-sou': [square, [[0, 0], [1, 1]], 'x'] } }));
    expect(s.masks['cam-sou']?.polygons).toEqual([square]);
    const cleared = reducer(s, { kind: 'events', envelopes: [env('perception', { masks: { 'cam-sou': [] } })], dropped: 0 });
    expect(cleared.masks['cam-sou']?.polygons).toEqual([]);
  });

  it('counts dropped messages', () => {
    expect(reducer(initialState, { kind: 'events', envelopes: [], dropped: 2 }).dropped).toBe(2);
  });

  it('drops a world event whose data the panels cannot read, and keeps going', () => {
    const before = fold(ONE_OF_EACH.plant_measurements, ONE_OF_EACH.tick);
    const bad = [
      env('plant_measurements', { ...measurement('spotcool', 5, 1), zones: null }),
      env('tick', { index: 1, clock: '07:15', day_of_year: 200 }), // no step_s
      env('tick', { index: 1, step_s: 'x', clock: '07:15', day_of_year: 200 }),
      env('plant_measurements', { ...measurement('spotcool', Number.NaN, 1) }),
      env('kpi', { run: 'spotcool', boptest: null }),
      env('scenario_state', { zones: [{ zone: 'mars', scene: 'x', people: 1 }] }),
      env('calendar', { room: 'meeting-south', items: [{ status: 'busy' }] }),
      env('metrics', { name: 'm', value: 'fast', unit: 's' }),
      env('vrf_state', { ...vrf(-30), profile: 7 }),
      env('complaint_cue', { ...cue(false, null), zone: 'nowhere' }),
      env('bms_write', { ...ONE_OF_EACH.bms_write.data, object_name: { a: 1 } }),
      env('run_status', { ...status('r1', 'running'), status: 'exploded' }),
    ];
    const after = reducer(before, { kind: 'events', envelopes: bad, dropped: 0 });
    expect(after.dropped).toBe(bad.length);
    expect({ ...after, dropped: 0 }).toEqual({ ...before, dropped: 0 }); // nothing else changed
    expect(fold(...Object.values(ONE_OF_EACH)).dropped).toBe(0); // the contract's shapes pass
  });
});

describe('list helpers', () => {
  it('insertBy keeps the newest MAX_ROWS', () => {
    let list: number[] = [];
    for (let i = 0; i < MAX_ROWS + 5; i++) list = insertBy(list, i, (x) => x);
    expect(list).toHaveLength(MAX_ROWS);
    expect(list[0]).toBe(5);
  });

  it('upsertByT inserts in order', () => {
    expect(upsertByT([{ t: 1 }, { t: 3 }], { t: 2 })).toEqual([{ t: 1 }, { t: 2 }, { t: 3 }]);
  });
});

describe('parseEnvelope', () => {
  const good = { v: 1, type: 'tick', source: 'world', t_ms: 1, sim_time_s: 2, data: {} };
  const text = (o: object) => JSON.stringify(o);

  it('accepts an envelope and fills a missing sim time with null', () => {
    expect(parseEnvelope(text(good))).toEqual(good);
    expect(parseEnvelope(text({ ...good, sim_time_s: undefined }))?.sim_time_s).toBeNull();
  });

  it.each([
    ['not JSON', '{'],
    ['another version', text({ ...good, v: 2 })],
    ['an unknown type', text({ ...good, type: 'nope' })],
    ['an agent type from the world', text({ ...good, type: 'perception' })],
    ['a world type from the agent', text({ ...good, source: 'agent' })],
    ['no t_ms', text({ ...good, t_ms: 'x' })],
    ['a bad sim time', text({ ...good, sim_time_s: 'x' })],
    ['data that is not an object', text({ ...good, data: [] })],
    ['a message over 1 MB', text({ ...good, data: { blob: 'x'.repeat(MAX_MESSAGE_BYTES) } })],
    ['a message over 1 MB in UTF-8', text({ ...good, data: { blob: 'é'.repeat(MAX_MESSAGE_BYTES / 2) } })],
  ])('refuses %s', (_, message) => {
    expect(parseEnvelope(message)).toBeNull();
  });

  it('reads a message just under 1 MB', () => {
    const message = text({ ...good, data: { blob: '' } });
    const padded = text({ ...good, data: { blob: 'x'.repeat(MAX_MESSAGE_BYTES - message.length) } });
    expect(padded.length).toBe(MAX_MESSAGE_BYTES);
    expect(parseEnvelope(padded)).not.toBeNull();
  });
});
