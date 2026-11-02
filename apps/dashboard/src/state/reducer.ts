// The dashboard's state, and the one pure reducer that folds /ws/events envelopes into it.
//
// Order rules, so events that arrive out of order never show stale data:
// - the world's physical state (measurements, KPIs, scenes, bookings) keeps the latest sim time;
// - wall-clock state (the VRF unit, the agent's perception) keeps the latest `t_ms`;
// - lists (BACnet log, agent feed, errors) are kept in `t_ms` order, a late event inserted where
//   its `t_ms` puts it, and hold the newest `MAX_ROWS`.
// Every new connection starts from a clear screen: the world sends each new viewer the run so
// far, so nothing is shown twice. A world event whose data lacks what the panels read is dropped
// and counted (`guards.ts`).
import type {
  BmsWriteData,
  CalendarData,
  CameraSceneData,
  ComplaintCueData,
  Envelope,
  ErrorData,
  KpiData,
  MetricsData,
  PlantMeasurementsData,
  Run,
  RunStatusData,
  ScenarioStateData,
  ScheduleItem,
  TickData,
  VrfStateData,
  Zone,
  ZoneMeasurement,
  ZoneScene,
} from '../wire';
import { isRecord } from '../wire';
import { isZone } from '../zones';
import { isWorldType, VALID } from './guards';

export const MAX_ROWS = 500;

export type Connection = 'connecting' | 'live' | 'reconnecting';
export type RunStatus = RunStatusData['status'];
export type ComplaintStatus = 'scheduled' | 'delivered' | 'failed';
export type Polygon = Array<[number, number]>;

export interface RunInfo {
  id: string;
  status: RunStatus;
  plant: string;
  scenario: string;
  message: string | null;
}

/** One plant run's total power (the mean over the step ending at `t`) and its energy so far. */
export interface PowerPoint {
  t: number;
  kw: number;
  kwh: number;
}

export interface Measured {
  t: number;
  zones: ZoneMeasurement[];
  powerKw: Record<string, number>;
  totalKw: number;
  energyKwh: number;
  outdoorC: number | null;
}

export interface Complaint {
  t: number | null;
  zone: Zone;
  text: string;
  channel: ComplaintCueData['channel'];
  status: ComplaintStatus;
  detail: string | null;
}

export interface Row<T> {
  /** Unique within the state: a stable key for the panels. */
  id: number;
  tMs: number;
  t: number | null;
  data: T;
}

export type AgentType = 'decision' | 'explanation' | 'chat';

export interface AgentItem extends Row<Record<string, unknown>> {
  type: AgentType;
  stub: boolean;
}

/** The agent's people count for a zone. */
export interface Sighting {
  people: number;
  tMs: number;
  stub: boolean;
}

/** The agent's person masks for a camera, in the camera's frame pixels. */
export interface Masks {
  polygons: Polygon[];
  tMs: number;
  stub: boolean;
}

export interface DashboardState {
  connection: Connection;
  attempt: number;
  /** Messages that were not valid envelopes, or world events the panels could not read. */
  dropped: number;
  /** Counts the clears: the panels' error boundaries start afresh with each. */
  epoch: number;
  /** The next row id. */
  nextId: number;
  run: RunInfo | null;
  /** The end of the latest step closed (its `tick`), or the run's start before the first one. */
  simNow: number | null;
  stepS: number | null;
  power: Record<Run, PowerPoint[]>;
  latest: Record<Run, Measured | null>;
  kpi: Record<Run, { data: KpiData; t: number | null } | null>;
  /** Ground truth from `scenario_state`: operator view only, labelled "script". */
  script: { t: number; zones: ZoneScene[]; activeEvents: string[] } | null;
  /** Door contact samples from `scenario_state`, by zone, in sim-time order. */
  doors: Partial<Record<Zone, Array<{ t: number; open: boolean }>>>;
  /** Ground truth from `camera_scene`, by camera id: operator view only. */
  scenes: Record<string, { zone: Zone | null; scene: string; t: number | null }>;
  calendar: Record<string, { items: ScheduleItem[]; t: number | null }>;
  complaints: Complaint[];
  bacnet: Array<Row<BmsWriteData>>;
  vrf: { data: VrfStateData; tMs: number } | null;
  people: Partial<Record<Zone, Sighting>>;
  masks: Record<string, Masks>;
  agent: AgentItem[];
  /** True once any agent event of the run said `stub: true`. */
  stubAgent: boolean;
  errors: Array<Row<ErrorData>>;
  metrics: Record<string, Row<MetricsData>>;
}

export type Action =
  | { kind: 'events'; envelopes: Envelope[]; dropped: number }
  | { kind: 'connection'; connection: Connection; attempt: number };

const FINAL: ReadonlySet<RunStatus> = new Set(['finished', 'cancelled', 'failed']);

export function isFinal(status: RunStatus): boolean {
  return FINAL.has(status);
}

export const initialState: DashboardState = {
  connection: 'connecting',
  attempt: 0,
  dropped: 0,
  epoch: 0,
  nextId: 0,
  run: null,
  simNow: null,
  stepS: null,
  power: { spotcool: [], baseline: [] },
  latest: { spotcool: null, baseline: null },
  kpi: { spotcool: null, baseline: null },
  script: null,
  doors: {},
  scenes: {},
  calendar: {},
  complaints: [],
  bacnet: [],
  vrf: null,
  people: {},
  masks: {},
  agent: [],
  stubAgent: false,
  errors: [],
  metrics: {},
};

/** Everything of a run cleared, the connection, the counts and the row ids kept. */
function cleared(state: DashboardState): DashboardState {
  return {
    ...initialState,
    connection: state.connection,
    attempt: state.attempt,
    dropped: state.dropped,
    epoch: state.epoch + 1,
    nextId: state.nextId,
  };
}

export function reducer(state: DashboardState, action: Action): DashboardState {
  if (action.kind === 'connection') {
    // A new connection gets the run so far from the world: keeping the old rows would show them twice.
    const base = action.connection === 'live' ? cleared(state) : state;
    return { ...base, connection: action.connection, attempt: action.attempt };
  }
  const folded = action.envelopes.reduce(applyEvent, state);
  return action.dropped ? { ...folded, dropped: folded.dropped + action.dropped } : folded;
}

/** Sim times compare when both are known; an unknown one counts as newer. */
function notOlder(t: number | null, than: number | null): boolean {
  return t === null || than === null || t >= than;
}

/** `list` with `item` after every element whose key is not larger (stable), the newest `max` kept. */
export function insertBy<T>(list: readonly T[], item: T, key: (x: T) => number, max = MAX_ROWS): T[] {
  let i = list.length;
  for (; i > 0; i--) {
    const before = list[i - 1];
    if (before === undefined || key(before) <= key(item)) break;
  }
  const next = [...list.slice(0, i), item, ...list.slice(i)];
  return next.length > max ? next.slice(next.length - max) : next;
}

/** `list` sorted by `t` with `item` in place, replacing an element with the same `t`. */
export function upsertByT<T extends { t: number }>(list: readonly T[], item: T): T[] {
  const i = list.findIndex((x) => x.t >= item.t);
  if (i === -1) return [...list, item];
  if (list[i]?.t === item.t) return list.map((x, j) => (j === i ? item : x));
  return [...list.slice(0, i), item, ...list.slice(i)];
}

export function applyEvent(state: DashboardState, env: Envelope): DashboardState {
  if (isWorldType(env.type) && !VALID[env.type](env.data)) return { ...state, dropped: state.dropped + 1 };
  const t = env.sim_time_s;
  const tMs = env.t_ms;
  const id = state.nextId;
  // Checked above, against what the panels read; the world validates the rest.
  const data: unknown = env.data;
  switch (env.type) {
    case 'tick': {
      const tick = data as TickData;
      if (t === null) return state;
      return { ...state, simNow: Math.max(state.simNow ?? -Infinity, t + tick.step_s), stepS: tick.step_s };
    }
    case 'run_status':
      return runStatus(state, data as RunStatusData, t);
    case 'scenario_state': {
      const scenario = data as ScenarioStateData;
      if (t === null) return state;
      const doors = { ...state.doors };
      for (const zone of scenario.zones) {
        if (typeof zone.door_open === 'boolean') {
          doors[zone.zone] = upsertByT(doors[zone.zone] ?? [], { t, open: zone.door_open });
        }
      }
      const script =
        state.script && state.script.t > t
          ? state.script
          : { t, zones: scenario.zones, activeEvents: scenario.active_events ?? [] };
      return { ...state, doors, script };
    }
    case 'plant_measurements': {
      const plant = data as PlantMeasurementsData;
      if (t === null) return state;
      const point = { t, kw: plant.total_power_kw, kwh: plant.energy_kwh };
      const held = state.latest[plant.run];
      const latest =
        held && held.t > t
          ? held
          : {
              t,
              zones: plant.zones,
              powerKw: plant.power_kw,
              totalKw: plant.total_power_kw,
              energyKwh: plant.energy_kwh,
              outdoorC: plant.outdoor_temp_c ?? null,
            };
      return {
        ...state,
        power: { ...state.power, [plant.run]: upsertByT(state.power[plant.run], point) },
        latest: { ...state.latest, [plant.run]: latest },
      };
    }
    case 'bms_write':
      return {
        ...state,
        nextId: id + 1,
        bacnet: insertBy(state.bacnet, { id, tMs, t, data: data as BmsWriteData }, byTMs),
      };
    case 'kpi': {
      const kpi = data as KpiData;
      const held = state.kpi[kpi.run];
      // A final KPI is never replaced by a provisional one; otherwise the latest sim time wins.
      const rank = (k: KpiData) => (k.provisional === false ? 1 : 0);
      if (held && (rank(held.data) > rank(kpi) || (rank(held.data) === rank(kpi) && !notOlder(t, held.t)))) {
        return state;
      }
      return { ...state, kpi: { ...state.kpi, [kpi.run]: { data: kpi, t } } };
    }
    case 'vrf_state':
      if (state.vrf && state.vrf.tMs > tMs) return state;
      return { ...state, vrf: { data: data as VrfStateData, tMs } };
    case 'camera_scene': {
      const scene = data as CameraSceneData;
      const held = state.scenes[scene.camera_id];
      if (held && !notOlder(t, held.t)) return state;
      return {
        ...state,
        scenes: { ...state.scenes, [scene.camera_id]: { zone: scene.zone, scene: scene.scene, t } },
      };
    }
    case 'calendar': {
      const calendar = data as CalendarData;
      const held = state.calendar[calendar.room];
      if (held && !notOlder(t, held.t)) return state;
      return { ...state, calendar: { ...state.calendar, [calendar.room]: { items: calendar.items, t } } };
    }
    case 'complaint_cue':
      return { ...state, complaints: complaint(state.complaints, data as ComplaintCueData, t) };
    case 'error':
      return { ...state, nextId: id + 1, errors: insertBy(state.errors, { id, tMs, t, data: data as ErrorData }, byTMs) };
    case 'metrics': {
      const metric = data as MetricsData;
      const labels = Object.entries(metric.labels ?? {}).sort(([a], [b]) => a.localeCompare(b));
      const key = `${metric.name} ${labels.map(([k, v]) => `${k}=${v}`).join(' ')}`.trim();
      const held = state.metrics[key];
      if (held && held.tMs > tMs) return state;
      return { ...state, metrics: { ...state.metrics, [key]: { id: held?.id ?? id, tMs, t, data: metric } }, nextId: id + 1 };
    }
    case 'perception':
      return perception(state, env.data, tMs);
    case 'decision':
    case 'explanation':
    case 'chat': {
      const stub = env.data.stub === true;
      const item: AgentItem = { id, type: env.type, tMs, t, data: env.data, stub };
      return {
        ...state,
        nextId: id + 1,
        agent: insertBy(state.agent, item, byTMs),
        stubAgent: state.stubAgent || stub,
      };
    }
    default: {
      const unknownType: never = env.type;
      return unknownType;
    }
  }
}

function byTMs(row: { tMs: number }): number {
  return row.tMs;
}

function runStatus(state: DashboardState, status: RunStatusData, t: number | null): DashboardState {
  // A different run id is a new run: nothing of the last one may stay on screen.
  const base = state.run && state.run.id !== status.run_id ? cleared(state) : state;
  // A late "running" never reopens a run that has ended.
  if (base.run && isFinal(base.run.status) && !isFinal(status.status)) return base;
  const run: RunInfo = {
    id: status.run_id,
    status: status.status,
    plant: status.plant,
    scenario: status.scenario,
    message: status.message ?? null,
  };
  const simNow = t === null ? base.simNow : Math.max(base.simNow ?? -Infinity, t);
  return { ...base, run, simNow };
}

/** The scheduled cue (`delivered: false`, no detail), then the occupant worker's result. */
export function complaintStatus(cue: ComplaintCueData): ComplaintStatus {
  if (cue.delivered) return 'delivered';
  return cue.detail == null ? 'scheduled' : 'failed';
}

function complaint(list: Complaint[], cue: ComplaintCueData, t: number | null): Complaint[] {
  const item: Complaint = {
    t,
    zone: cue.zone,
    text: cue.text,
    channel: cue.channel,
    status: complaintStatus(cue),
    detail: cue.detail ?? null,
  };
  const i = list.findIndex((c) => c.t === t && c.zone === cue.zone && c.text === cue.text);
  if (i === -1) return insertBy(list, item, (c) => c.t ?? Infinity);
  // A result may arrive before its scheduled cue: a known outcome never goes back to scheduled.
  if (item.status === 'scheduled') return list;
  return list.map((c, j) => (j === i ? item : c));
}

function perception(state: DashboardState, data: Record<string, unknown>, tMs: number): DashboardState {
  const stub = data.stub === true;
  const people = { ...state.people };
  if (isRecord(data.people)) {
    for (const [zone, count] of Object.entries(data.people)) {
      const held = isZone(zone) ? people[zone] : undefined;
      if (isZone(zone) && Number.isInteger(count) && (count as number) >= 0 && !(held && held.tMs > tMs)) {
        people[zone] = { people: count as number, tMs, stub };
      }
    }
  }
  const masks = { ...state.masks };
  if (isRecord(data.masks)) {
    for (const [camera, polygons] of Object.entries(data.masks)) {
      const held = masks[camera];
      if (Array.isArray(polygons) && !(held && held.tMs > tMs)) {
        masks[camera] = { polygons: polygons.filter(isPolygon), tMs, stub };
      }
    }
  }
  return { ...state, people, masks, stubAgent: state.stubAgent || stub };
}

function isPolygon(value: unknown): value is Polygon {
  return (
    Array.isArray(value) &&
    value.length >= 3 &&
    value.every(
      (p) =>
        Array.isArray(p) &&
        p.length === 2 &&
        p.every((n) => typeof n === 'number' && Number.isFinite(n)),
    )
  );
}
