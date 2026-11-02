// Writes fixtures/sample-events.jsonl: a small synthetic day in WP6's recording format (event
// records only), covering every event type, the agent's included. Deterministic: run it again and
// the file is unchanged. Typed against the generated contracts, so `tsc -b` fails when they drift.
//
//   node scripts/make-fixture.ts
//
// The numbers are illustrative, not physics. Two people work late in the core and the receptionist
// stays, so the day's end (what the screenshot tests capture) still shows people and masks. The agent is labelled `stub: true`, and its
// perception carries person masks so the camera overlay is exercised. The VRF unit reports as an LG
// (swing only, no horizontal louver) in the morning and as a Mitsubishi from the 14:15 complaint
// on, so both louver drawings are covered; a real run keeps one brand.
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type {
  BmsWriteData,
  CalendarData,
  CameraSceneData,
  ComplaintCueData,
  ErrorData,
  EventType,
  KpiData,
  MetricsData,
  PlantMeasurementsData,
  Run,
  RunStatusData,
  ScenarioStateData,
  TickData,
  VrfStateData,
  Zone,
} from '../src/wire.ts';

const DAY_S = 199 * 86_400; // BOPTEST's peak cooling day
const STEP_S = 900;
const START_S = DAY_S + 7 * 3600;
const STEPS = 48; // 07:00 to 19:00
const RUN_ID = 'fixture-hybrid-day';
const ZONES: Zone[] = ['cor', 'nor', 'sou', 'eas', 'wes'];
const WARMTH: Record<Zone, number> = { cor: 0, nor: 0.4, sou: 0.9, eas: 0.6, wes: 1.1 }; // sun and walls
const METERS = ['AHU-SF-PWR', 'AHU-CLG-PUMP-PWR', 'AHU-HTG-PUMP-PWR', 'CH-PWR', 'CHW-PUMP-PWR', 'HP-PWR', 'HW-PUMP-PWR'];
const BMS = 'SpotCool-Office-BMS';
const STUB_CLIENT = '127.0.0.1:50510';

const hm = (h: number, m = 0) => DAY_S + h * 3600 + m * 60;
const r3 = (x: number) => Math.round(x * 1000) / 1000;
const r2 = (x: number) => Math.round(x * 100) / 100;

/** People per zone over the day: [from, count] steps, as a scenario script would say. */
const PEOPLE: Record<Zone, Array<[number, number]>> = {
  cor: [[hm(7), 0], [hm(8, 30), 4], [hm(12, 30), 1], [hm(13, 30), 4], [hm(17, 30), 2]],
  nor: [[hm(7), 0], [hm(8, 30), 2], [hm(12, 30), 0], [hm(13, 30), 2], [hm(18), 0]],
  sou: [[hm(7), 0], [hm(14), 5], [hm(15), 0]],
  eas: [[hm(7), 0]],
  wes: [[hm(7), 0], [hm(8, 30), 1]],
};
const SCENES: Record<Zone, Array<[number, string]>> = {
  cor: [[hm(7), 'empty'], [hm(8, 30), 'many'], [hm(12, 30), 'few'], [hm(13, 30), 'many'], [hm(17, 30), 'few']],
  nor: [[hm(7), 'empty'], [hm(8, 30), 'few'], [hm(12, 30), 'empty'], [hm(13, 30), 'few'], [hm(18), 'empty']],
  sou: [[hm(7), 'empty'], [hm(14), 'meeting'], [hm(14, 15), 'complaint'], [hm(15), 'empty']],
  eas: [[hm(7), 'empty']],
  wes: [[hm(7), 'empty'], [hm(8, 30), 'few'], [hm(10), 'door_open'], [hm(10, 45), 'few']],
};

function at<T>(steps: Array<[number, T]>, t: number): T {
  let value = steps[0]?.[1];
  for (const [from, v] of steps) if (from <= t) value = v;
  if (value === undefined) throw new Error('empty schedule');
  return value;
}

/** Person outlines on a 1280x720 frame, one per person, spread across it. */
function masks(people: number): Array<Array<[number, number]>> {
  return Array.from({ length: people }, (_, i) => {
    const x = 180 + i * (920 / Math.max(1, people - 1 || 1));
    return [
      [x - 18, 250], [x + 18, 250], [x + 26, 300], [x + 60, 340], [x + 64, 620],
      [x - 64, 620], [x - 60, 340], [x - 26, 300],
    ];
  });
}

export function makeFixture(): string[] {
  const lines: string[] = [];
  let tMs = 1_793_600_000_000;
  const emit = (type: EventType, data: object, t: number | null, source: 'world' | 'agent' = 'world') => {
    tMs += 7;
    lines.push(JSON.stringify({ kind: 'event', envelope: { v: 1, type, source, t_ms: tMs, sim_time_s: t, data } }));
  };
  const energy: Record<Run, number> = { spotcool: 0, baseline: 0 };
  const held = new Map<Zone, boolean>(); // the stub's view: is the zone held as empty?

  const measurements = (run: Run, t: number, first: boolean) => {
    const hour = (t - DAY_S) / 3600;
    const load = 6 + 9 * Math.exp(-(((hour - 14) / 3.5) ** 2)); // peaks at 14:00
    const empty = ZONES.filter((z) => at(PEOPLE[z], t - STEP_S) === 0).length;
    const kw = run === 'baseline' ? load : load * (1 - 0.09 * empty);
    if (!first) energy[run] += (kw * STEP_S) / 3600;
    const zones = ZONES.map((zone) => {
      const vacant = run === 'spotcool' && at(PEOPLE[zone], t - STEP_S) === 0;
      const temp = vacant ? 24 + (1.6 + WARMTH[zone]) * (1 - Math.exp(-(hour - 7) / 2)) : 23.7 + 0.3 * WARMTH[zone] + 0.2 * Math.sin(hour);
      return {
        zone,
        temp_c: r2(temp),
        cooling_sp_c: vacant && hour < 19 ? 28 : 24,
        heating_sp_c: 20,
        co2_ppm: 420 + 60 * at(PEOPLE[zone], t - STEP_S),
        airflow_lps: vacant ? 60 : 210 + 20 * at(PEOPLE[zone], t - STEP_S),
      };
    });
    const share = [0.04, 0.06, 0, 0.8, 0.1, 0, 0];
    const power_kw = Object.fromEntries(METERS.map((m, i) => [m, r2(kw * (share[i] ?? 0))]));
    const data: PlantMeasurementsData = {
      run,
      zones,
      power_kw,
      total_power_kw: r3(Object.values(power_kw).reduce((a, b) => a + b, 0)),
      energy_kwh: r3(energy[run]),
      outdoor_temp_c: r2(26 + 6 * Math.sin(((hour - 9) / 12) * Math.PI)),
    };
    emit('plant_measurements', data, t);
  };

  const write = (t: number, object_name: string, object_id: string, value: BmsWriteData['value'], extra: Partial<BmsWriteData> = {}) => {
    const data: BmsWriteData = { device: BMS, object_id, object_name, priority: 10, value, client: STUB_CLIENT, accepted: true, reason: null, ...extra };
    emit('bms_write', data, t);
  };

  const vrf = (t: number, state: Partial<VrfStateData>) => {
    const data: VrfStateData = {
      profile: 'lg', power_on: true, mode: 'Cool', setpoint_c: 24, fan: 'Auto', vane_v_pos: 'Swing',
      vane_h_pos: null, vane_v_angle_deg: -30, vane_h_angle_deg: null, room_temp_c: 25.2, power_kw: 1.1, ...state,
    };
    emit('vrf_state', data, t);
  };

  measurements('spotcool', START_S, true);
  measurements('baseline', START_S, true);
  const status = (s: RunStatusData['status'], t: number) => {
    const data: RunStatusData = { run_id: RUN_ID, status: s, plant: 'rc', scenario: 'hybrid_tuesday', message: null };
    emit('run_status', data, t);
  };
  status('running', START_S);

  for (let i = 0; i < STEPS; i++) {
    const t = START_S + i * STEP_S;
    const state: ScenarioStateData = {
      zones: ZONES.map((zone) => ({
        zone,
        scene: at(SCENES[zone], t),
        people: at(PEOPLE[zone], t),
        door_open: zone === 'wes' ? t >= hm(10) && t < hm(10, 45) : null,
        clothing: zone === 'sou' && at(PEOPLE.sou, t) > 0 ? (t >= hm(14, 30) ? 'heavy' : 'light') : null,
      })),
      active_events: [
        ...(t >= hm(10) && t < hm(10, 45) ? ['door_open:wes'] : []),
        ...(t >= hm(14) && t < hm(15) ? ['booking:meeting-south'] : []),
      ],
    };
    emit('scenario_state', state, t);
    const people = Object.fromEntries(ZONES.map((z) => [z, at(PEOPLE[z], t)]));
    // Masks only for the cameras whose count changed: the dashboard keeps each camera's last ones.
    const changed = ZONES.filter((z) => i === 0 || at(PEOPLE[z], t) !== at(PEOPLE[z], t - STEP_S));
    const masked = Object.fromEntries(changed.map((z) => [`cam-${z}`, masks(at(PEOPLE[z], t))]));
    emit('perception', { stub: true, people, masks: masked }, t, 'agent');

    for (const zone of ZONES) {
      const scene = at(SCENES[zone], t);
      if (i === 0 || at(SCENES[zone], t - STEP_S) !== scene) {
        const data: CameraSceneData = { camera_id: `cam-${zone}`, zone, scene };
        emit('camera_scene', data, t);
      }
    }
    if (i === 0 || t === hm(13, 45)) {
      const items: CalendarData['items'] =
        t < hm(13, 45)
          ? []
          : [{ status: 'busy', subject: 'Quarterly review', location: 'meeting-south', start: { dateTime: '2026-07-19T14:00:00', timeZone: 'UTC' }, end: { dateTime: '2026-07-19T15:00:00', timeZone: 'UTC' }, isPrivate: false }];
      const data: CalendarData = { room: 'meeting-south', items };
      emit('calendar', data, t);
    }
    if (i === 0) {
      write(t, 'SPOTCOOL-HEARTBEAT', 'binary-value,901', 'On');
      vrf(t, {});
    }
    // The stub's policy: an empty zone goes on standby at 28 degC; a zone with people (or the
    // booked meeting room from 13:30) is released to the schedule.
    for (const [k, zone] of ZONES.entries()) {
      const occupied = at(PEOPLE[zone], t) > 0 || (zone === 'sou' && t >= hm(13, 30) && t < hm(15));
      const empty = !occupied && t < hm(19);
      if (held.get(zone) === empty) continue;
      held.set(zone, empty);
      const reason = empty ? 'the zone is empty' : zone === 'sou' && at(PEOPLE.sou, t) === 0 ? 'pre-cooling for a booking' : 'people are present';
      const point = zone.toUpperCase();
      const sensor = empty ? 'Unoccupied' : 'Occupied';
      const sp = empty ? 28 : null;
      emit('decision', { stub: true, zone, point: `ZN-${point}-OCC-SENSOR`, value: sensor, priority: 10, reason }, t, 'agent');
      emit('decision', { stub: true, zone, point: `ZN-${point}-CLG-SP`, value: sp, priority: 10, reason }, t, 'agent');
      const done = `ZN-${point}-OCC-SENSOR ${sensor}, ZN-${point}-CLG-SP ${sp === null ? 'released' : sp.toFixed(1)}`;
      emit('explanation', { stub: true, text: `${zone === 'sou' ? 'South' : point}: ${reason} -> ${done} at priority 10` }, t, 'agent');
      write(t, `ZN-${point}-OCC-SENSOR`, `binary-value,${450 + k}`, sensor);
      write(t, `ZN-${point}-CLG-SP`, `analog-value,${200 + k}`, sp);
    }
    if (t === hm(9)) {
      write(t, 'ZN-COR-CLG-SP', 'analog-value,200', 22, { priority: 5, client: '192.168.1.40:47808', accepted: false, reason: 'priority 5 is not writable: priorities 1-7 are reserved' });
    }
    if (t === hm(14, 15)) {
      const cue: ComplaintCueData = { zone: 'sou', text: "It's freezing in this meeting room.", channel: 'voice', delivered: false, detail: null };
      emit('complaint_cue', cue, t);
      // The agent re-aims the meeting room's louvers; the unit slews at 15 deg/s.
      write(t, 'IDU-VANE-V', 'multi-state-value,5', 'Pos2', { device: 'VRF-Gateway-MeetingRoom' });
      for (let k = 0; k <= 5; k++) {
        vrf(t, { profile: 'mitsubishi', vane_v_pos: 'Pos2', vane_h_pos: 'Pos4', vane_v_angle_deg: r3(-30 + 3 * k), vane_h_angle_deg: r3(3 * k), setpoint_c: 25, fan: 'Low', room_temp_c: 23.6, power_kw: 0.9 });
      }
    }
    if (t === hm(16)) {
      const cue: ComplaintCueData = { zone: 'cor', text: 'Could the open-plan area be a bit cooler?', channel: 'chat', delivered: false, detail: null };
      emit('complaint_cue', cue, t);
    }
    if (t === hm(12)) {
      const error: ErrorData = { where: 'cameras', message: 'camera cam-eas: frame source failed (OSError: clip unreadable); synthetic frames', fatal: false };
      emit('error', error, t);
    }

    const next = t + STEP_S;
    measurements('spotcool', next, false);
    for (const run of i % 4 === 0 ? (['spotcool', 'baseline'] as Run[]) : []) {
      const latency: MetricsData = { name: 'plant_step_latency', value: r3(0.002 + 0.0001 * (i % 5)), unit: 's', labels: { run } };
      emit('metrics', latency, t);
    }
    measurements('baseline', next, false);
    if ((i + 1) % 4 === 0) kpis(emit, next, energy, true);
    if (t === hm(14, 15)) {
      const delivered: ComplaintCueData = { zone: 'sou', text: "It's freezing in this meeting room.", channel: 'voice', delivered: true, detail: null };
      emit('complaint_cue', delivered, t);
      emit('chat', { stub: true, text: 'Heard: too cold in the meeting room. Raising its setpoint 1 K and aiming the louvers away from the table.' }, t, 'agent');
    }
    if (t === hm(16)) {
      const failed: ComplaintCueData = { zone: 'cor', text: 'Could the open-plan area be a bit cooler?', channel: 'chat', delivered: false, detail: 'POST /chat: ConnectError: All connection attempts failed (after 3 attempts)' };
      emit('complaint_cue', failed, t);
    }
    const tick: TickData = { index: i, step_s: STEP_S, clock: clock(t), day_of_year: 200 };
    emit('tick', tick, t);
  }
  const end = START_S + STEPS * STEP_S;
  status('finished', end);
  kpis(emit, end, energy, false);
  return lines;
}

function clock(t: number): string {
  const s = t % 86_400;
  return `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`;
}

function kpis(emit: (type: EventType, data: object, t: number | null) => void, t: number, energy: Record<Run, number>, provisional: boolean) {
  const saved = energy.baseline > 0 ? r3((1 - energy.spotcool / energy.baseline) * 100) : null;
  const progress = (t - START_S) / (STEPS * STEP_S);
  const boptest = (run: Run): KpiData['boptest'] =>
    provisional
      ? {}
      : { ener_tot: r3(energy[run] / 1662.66), tdis_tot: run === 'spotcool' ? 10.3 : 0.15, idis_tot: run === 'spotcool' ? 30.5 : 0, cost_tot: null, time_rat: null };
  const spotcool: KpiData = { run: 'spotcool', boptest: boptest('spotcool'), comfort_where_people_kh: r3(1.37 * progress), energy_saved_pct: saved, provisional };
  const baseline: KpiData = { run: 'baseline', boptest: boptest('baseline'), comfort_where_people_kh: r3(0.33 * progress), energy_saved_pct: null, provisional };
  emit('kpi', spotcool, t);
  emit('kpi', baseline, t);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const out = fileURLToPath(new URL('../fixtures/sample-events.jsonl', import.meta.url));
  const lines = makeFixture();
  writeFileSync(out, `${lines.join('\n')}\n`, 'utf8');
  console.log(`wrote ${lines.length} events to ${out}`);
}
