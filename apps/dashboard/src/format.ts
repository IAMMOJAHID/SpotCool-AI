// Formatting helpers. Simulation time is seconds since 1 January 00:00 of the BOPTEST year.

export const DAY_S = 86_400;
/** The calendar's day zero: a sim time is 2026-01-01 00:00 plus its seconds (contracts' calendar.py). */
export const SIM_EPOCH_MS = Date.UTC(2026, 0, 1);

const pad = (n: number) => String(n).padStart(2, '0');

/** Time of day, `HH:MM`, as the world's `SimClock.clock_text`. */
export function clockText(t: number): string {
  const s = ((Math.round(t) % DAY_S) + DAY_S) % DAY_S;
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}`;
}

/** Day of the BOPTEST year, 1-based, as the world's `SimClock.day_of_year`. */
export function dayOfYear(t: number): number {
  return (Math.floor(t / DAY_S) % 365) + 1;
}

/** The simulation calendar's date, such as `19 Jul` for day 200. */
export function calendarDate(t: number): string {
  return new Date(SIM_EPOCH_MS + t * 1000).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
}

/** A calendar date-time (`2026-07-19T14:00:00`, labelled UTC) as sim seconds, or null. */
export function simSeconds(dateTime: string): number | null {
  const zoned = /(?:[zZ]|[+-]\d\d:\d\d)$/.test(dateTime) ? dateTime : `${dateTime}Z`;
  const ms = Date.parse(zoned);
  return Number.isNaN(ms) ? null : (ms - SIM_EPOCH_MS) / 1000;
}

const PLANTS: Record<string, string> = {
  'boptest-local': 'BOPTEST (local)',
  'boptest-hosted': 'BOPTEST (hosted)',
  rc: 'RC model',
  replay: 'Replay',
};

export function plantLabel(plant: string): string {
  return PLANTS[plant] ?? plant;
}

/** `hybrid_tuesday` as `Hybrid Tuesday`. */
export function scenarioLabel(name: string): string {
  return name
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/** A number with fixed digits, or an em dash when there is none. */
export function fixed(value: number | null | undefined, digits: number): string {
  return value == null || !Number.isFinite(value) ? '\u2014' : value.toFixed(digits);
}

/** A BACnet write's value; a NULL write releases its priority slot. */
export function bmsValue(value: string | number | null): string {
  if (value === null) return 'NULL (release)';
  return typeof value === 'number' ? String(Number(value.toFixed(3))) : value;
}

export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
