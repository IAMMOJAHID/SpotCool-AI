// The wire: the contract types `npm run gen:types` generates from packages/contracts, and the
// one check every message from /ws/events passes before the reducer sees it.
import type {
  EventEnvelope,
  EventSource,
  EventType,
} from './contracts/types/EventEnvelope.schema';

export type { EventEnvelope, EventSource, EventType };
export type { BmsWriteData } from './contracts/types/BmsWriteData.schema';
export type { CalendarData, ScheduleItem } from './contracts/types/CalendarData.schema';
export type { CameraCatalog, CameraInfo } from './contracts/types/CameraCatalog.schema';
export type { CameraSceneData } from './contracts/types/CameraSceneData.schema';
export type { ComplaintCueData } from './contracts/types/ComplaintCueData.schema';
export type { ComplaintText } from './contracts/types/ComplaintText.schema';
export type { ErrorData } from './contracts/types/ErrorData.schema';
export type { KpiData } from './contracts/types/KpiData.schema';
export type { MetricsData } from './contracts/types/MetricsData.schema';
export type {
  PlantMeasurementsData,
  Run,
  Zone,
  ZoneMeasurement,
} from './contracts/types/PlantMeasurementsData.schema';
export type { RunStatusData } from './contracts/types/RunStatusData.schema';
export type { ScenarioStateData, ZoneScene } from './contracts/types/ScenarioStateData.schema';
export type { TickData } from './contracts/types/TickData.schema';
export type { VrfStateData } from './contracts/types/VrfStateData.schema';

/** An envelope that passed `parseEnvelope`: `data` is an object and `sim_time_s` is set or null. */
export type Envelope = EventEnvelope & { sim_time_s: number | null };

// A Record over the generated union: the compiler fails here when the contracts add a type.
const PUBLISHER: Record<EventType, EventSource> = {
  tick: 'world',
  run_status: 'world',
  scenario_state: 'world',
  plant_measurements: 'world',
  bms_write: 'world',
  kpi: 'world',
  vrf_state: 'world',
  camera_scene: 'world',
  calendar: 'world',
  complaint_cue: 'world',
  error: 'world',
  metrics: 'world',
  perception: 'agent',
  decision: 'agent',
  explanation: 'agent',
  chat: 'agent',
};

/** Every event type, in the contract's order. */
export const EVENT_TYPES = Object.keys(PUBLISHER) as EventType[];

/** The largest /ws/events message read; a longer one is dropped unparsed. */
export const MAX_MESSAGE_BYTES = 1024 * 1024;
const encoder = new TextEncoder();

/** Whether a message is longer than `MAX_MESSAGE_BYTES` in UTF-8 (at most 3 bytes per char). */
function tooLong(text: string): boolean {
  if (text.length > MAX_MESSAGE_BYTES) return true;
  return text.length * 3 > MAX_MESSAGE_BYTES && encoder.encode(text).length > MAX_MESSAGE_BYTES;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * One /ws/events message as an envelope, or null when it is not one: over `MAX_MESSAGE_BYTES`, a
 * version other than 1, an unknown type, a type from the wrong publisher (an agent type must
 * come from the agent), a missing time, or data that is not an object.
 */
export function parseEnvelope(text: string): Envelope | null {
  if (tooLong(text)) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(raw) || raw.v !== 1 || !isRecord(raw.data)) return null;
  const { type, source, t_ms: tMs, sim_time_s: simTime } = raw;
  if (typeof type !== 'string' || !Object.hasOwn(PUBLISHER, type)) return null;
  const known = type as EventType;
  if (source !== PUBLISHER[known]) return null;
  if (typeof tMs !== 'number' || !Number.isFinite(tMs)) return null;
  if (simTime != null && (typeof simTime !== 'number' || !Number.isFinite(simTime))) return null;
  return { v: 1, type: known, source: PUBLISHER[known], t_ms: tMs, sim_time_s: simTime ?? null, data: raw.data };
}
