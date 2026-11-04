import { _steppedLineTo } from 'chart.js/helpers';
import { describe, expect, it } from 'vitest';
import { chartData, POWER_STEPPED } from './EnergyChart';

const STEP = 900;

describe('the energy chart', () => {
  it("draws power with Chart.js's stepped mode 'after'", () => {
    expect(POWER_STEPPED).toBe('after');
    const power = { spotcool: [{ t: 0, kw: 1, kwh: 0 }], baseline: [{ t: 0, kw: 2, kwh: 0 }] };
    const datasets = chartData(power).datasets;
    expect(datasets.filter((d) => d.yAxisID === 'kw').map((d) => d.stepped)).toEqual(['after', 'after']);
    expect(datasets.filter((d) => d.yAxisID === 'kwh').map((d) => d.stepped)).toEqual([undefined, undefined]);
  });

  it('draws no energy on an RC run, only power', () => {
    const power = { spotcool: [{ t: 0, kw: 1, kwh: 5 }], baseline: [{ t: 0, kw: 2, kwh: 9 }] };
    expect(chartData(power, true).datasets.map((d) => d.label)).toEqual(['SpotCool kW', 'Baseline kW']);
    expect(chartData(power).datasets).toHaveLength(4);
  });

  it('draws a point stamped t (the mean over its step) over [t - step, t]', () => {
    // Chart.js's own line drawing between two neighbouring points, in its 'after' mode.
    const t = 10 * STEP;
    const previous = { x: t - STEP, y: 4 };
    const point = { x: t, y: 9 }; // 9 kW: the mean over the step that ended at t
    const lines: Array<[number, number]> = [];
    const ctx = { lineTo: (x: number, y: number) => lines.push([x, y]) } as unknown as CanvasRenderingContext2D;
    _steppedLineTo(ctx, previous, point, false, POWER_STEPPED);
    // The level 9 runs from t - step to t: the step the mean belongs to.
    expect(lines).toEqual([
      [t - STEP, 9],
      [t, 9],
    ]);
  });
});
