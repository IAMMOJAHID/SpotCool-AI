// Colours the canvas and SVG need as values (the rest live in styles.css).

export const RUN_COLOR = { spotcool: '#22d3ee', baseline: '#ff9f1c' } as const;

// Cool to warm over the range a cooled office spans: 24 degC is the baseline's setpoint, 28 degC
// the stub's standby setpoint.
const STOPS: ReadonlyArray<[number, [number, number, number]]> = [
  [22, [29, 78, 216]],
  [24, [56, 189, 248]],
  [26, [253, 230, 138]],
  [28, [251, 146, 60]],
  [30, [220, 38, 38]],
];

/** A zone's fill for its temperature, clamped to 22-30 degC. */
export function tempColor(tempC: number): string {
  const first = STOPS[0];
  const last = STOPS[STOPS.length - 1];
  if (!first || !last) return '#888';
  const t = Math.min(last[0], Math.max(first[0], tempC));
  for (let i = 1; i < STOPS.length; i++) {
    const lo = STOPS[i - 1];
    const hi = STOPS[i];
    if (lo && hi && t <= hi[0]) {
      const f = (t - lo[0]) / (hi[0] - lo[0]);
      const rgb = lo[1].map((c, k) => Math.round(c + ((hi[1][k] ?? c) - c) * f));
      return `rgb(${rgb.join(', ')})`;
    }
  }
  return `rgb(${last[1].join(', ')})`;
}

/** Dark or light text for a fill, by its relative luminance. */
export function inkFor(fill: string): string {
  const [r = 0, g = 0, b = 0] = (fill.match(/\d+/g) ?? []).map(Number);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 140 ? '#05070a' : '#ffffff';
}

/** The CSS gradient of the scale, for its legend. */
export function tempGradient(): string {
  return `linear-gradient(to right, ${STOPS.map(([c, rgb]) => `rgb(${rgb.join(',')}) ${((c - 22) / 8) * 100}%`).join(', ')})`;
}
