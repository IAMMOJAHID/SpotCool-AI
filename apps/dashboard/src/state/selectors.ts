// Views derived from the state: the timeline's intervals and the axis the timeline and chart share.
import { DAY_S, simSeconds } from '../format';
import type { DashboardState } from './reducer';

export interface Interval {
  from: number;
  /** null while it is still open. */
  to: number | null;
}

export interface Meeting extends Interval {
  room: string;
  subject: string | null;
}

/** Open intervals from door samples in sim-time order: open from a sample that reads open to the next that reads closed. */
export function doorIntervals(samples: ReadonlyArray<{ t: number; open: boolean }>): Interval[] {
  const intervals: Interval[] = [];
  let from: number | null = null;
  for (const sample of samples) {
    if (sample.open && from === null) {
      from = sample.t;
    } else if (!sample.open && from !== null) {
      intervals.push({ from, to: sample.t });
      from = null;
    }
  }
  if (from !== null) intervals.push({ from, to: null });
  return intervals;
}

/** Every room's bookings (anything but a free slot), in start order. */
export function meetings(calendar: DashboardState['calendar']): Meeting[] {
  const found: Meeting[] = [];
  for (const [room, { items }] of Object.entries(calendar)) {
    for (const item of items) {
      const from = simSeconds(item.start.dateTime);
      const to = simSeconds(item.end.dateTime);
      if (item.status !== 'free' && from !== null && to !== null) {
        found.push({ room, subject: item.isPrivate ? null : (item.subject ?? null), from, to });
      }
    }
  }
  return found.sort((a, b) => a.from - b.from);
}

/** The world's default day, `start_clock` 07:00 to `end_clock` 19:00 (`WorldSettings`). */
const DAY_START_S = 7 * 3600;
const DAY_END_S = 19 * 3600;

/** The sim-time axis of the timeline and the energy chart: the default day, widened to the data. */
export function dayWindow(state: DashboardState): [number, number] | null {
  const times = [state.simNow, ...Object.values(state.power).flatMap((points) => points.map((p) => p.t))].filter(
    (t): t is number => t !== null,
  );
  if (times.length === 0) return null;
  const first = Math.min(...times);
  const day = Math.floor(first / DAY_S) * DAY_S;
  return [Math.min(day + DAY_START_S, first), Math.max(day + DAY_END_S, ...times)];
}
