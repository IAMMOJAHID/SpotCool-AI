// What the reducer checks of a world event's data before it keeps it: the fields the panels
// read, compute with or map over. The world validates its payloads against the contracts; these
// checks keep one malformed event (a newer contract, a bug) from blanking a panel.
import type { EventType } from '../wire';
import { isRecord } from '../wire';
import { isZone } from '../zones';

type Check = (value: unknown) => boolean;
export type WorldType = Exclude<EventType, 'perception' | 'decision' | 'explanation' | 'chat'>;

const finite: Check = (x) => typeof x === 'number' && Number.isFinite(x);
const text: Check = (x) => typeof x === 'string';
const flag: Check = (x) => typeof x === 'boolean';
const zone: Check = (x) => typeof x === 'string' && isZone(x);
const maybe = (check: Check): Check => (x) => x == null || check(x);
const orNull = (check: Check): Check => (x) => x === null || check(x);
const listOf = (check: Check): Check => (x) => Array.isArray(x) && x.every(check);
const valuesOf = (check: Check): Check => (x) => isRecord(x) && Object.values(x).every(check);
const oneOf = (...values: string[]): Check => (x) => typeof x === 'string' && values.includes(x);
const shape = (fields: Record<string, Check>): Check => (x) =>
  isRecord(x) && Object.entries(fields).every(([key, check]) => check(x[key]));

const run = oneOf('spotcool', 'baseline');
const time = shape({ dateTime: text });

/** For each world event type, whether its data has what the dashboard reads. */
export const VALID: Record<WorldType, Check> = {
  tick: shape({ index: finite, step_s: (x) => finite(x) && (x as number) > 0 }),
  run_status: shape({
    run_id: text,
    status: oneOf('starting', 'running', 'paused', 'finished', 'cancelled', 'failed'),
    plant: text,
    scenario: text,
    message: maybe(text),
  }),
  scenario_state: shape({
    zones: listOf(shape({ zone, scene: text, people: (x) => Number.isInteger(x) && (x as number) >= 0, door_open: maybe(flag) })),
    active_events: maybe(listOf(text)),
  }),
  plant_measurements: shape({
    run,
    zones: listOf(
      shape({ zone, temp_c: finite, cooling_sp_c: finite, heating_sp_c: finite, co2_ppm: finite, airflow_lps: finite }),
    ),
    power_kw: valuesOf(finite),
    total_power_kw: finite,
    energy_kwh: finite,
    outdoor_temp_c: maybe(finite),
  }),
  bms_write: shape({
    device: text,
    object_id: text,
    object_name: text,
    priority: finite,
    value: (x) => x === null || text(x) || finite(x),
    client: text,
    accepted: flag,
    reason: maybe(text),
  }),
  kpi: shape({
    run,
    boptest: valuesOf(orNull(finite)),
    comfort_where_people_kh: maybe(finite),
    energy_saved_pct: maybe(finite),
    provisional: maybe(flag),
  }),
  vrf_state: shape({
    profile: text,
    power_on: flag,
    mode: text,
    setpoint_c: finite,
    fan: text,
    vane_v_pos: text,
    vane_h_pos: orNull(text),
    vane_v_angle_deg: finite,
    vane_h_angle_deg: orNull(finite),
    room_temp_c: finite,
    power_kw: finite,
  }),
  camera_scene: shape({ camera_id: text, zone: maybe(zone), scene: text }),
  calendar: shape({
    room: text,
    items: listOf(shape({ status: text, start: time, end: time, subject: maybe(text), isPrivate: maybe(flag) })),
  }),
  complaint_cue: shape({ zone, text, channel: oneOf('chat', 'voice'), delivered: flag, detail: maybe(text) }),
  error: shape({ where: text, message: text, fatal: maybe(flag) }),
  metrics: shape({ name: text, value: finite, unit: text, labels: maybe(valuesOf(text)) }),
};

export function isWorldType(type: EventType): type is WorldType {
  return Object.hasOwn(VALID, type);
}
