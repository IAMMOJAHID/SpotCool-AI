import { Chart, Legend, LinearScale, LineController, LineElement, PointElement, Tooltip } from 'chart.js';
import type { ChartData, ChartOptions } from 'chart.js';
import { memo, useMemo } from 'react';
import { Line } from 'react-chartjs-2';
import { RUN_COLOR } from '../colors';
import { clockText, fixed } from '../format';
import type { DashboardState } from '../state/reducer';
import type { Axis } from '../state/selectors';
import { Empty, Panel } from '../ui';
import type { Run } from '../wire';

Chart.register(LineController, LineElement, PointElement, LinearScale, Legend, Tooltip);

const RUNS: ReadonlyArray<[Run, string]> = [
  ['spotcool', 'SpotCool'],
  ['baseline', 'Baseline'],
];

/**
 * A point's power is the mean over the step that ended at its stamp. Chart.js's "after" draws a
 * point's value over the interval before it, so each mean covers its own step; "before" would
 * hold the earlier point's value over that interval, a step late.
 */
export const POWER_STEPPED = 'after';

/** Two sim-time series per run: power (each step's mean, drawn over that step) and energy. */
export function chartData(power: DashboardState['power']): ChartData<'line', Array<{ x: number; y: number }>> {
  return {
    datasets: RUNS.flatMap(([run, label]) => [
      {
        label: `${label} kW`,
        data: power[run].map((p) => ({ x: p.t, y: p.kw })),
        stepped: POWER_STEPPED,
        borderColor: RUN_COLOR[run],
        backgroundColor: RUN_COLOR[run],
        borderWidth: 3,
        pointRadius: 0,
        yAxisID: 'kw',
      },
      {
        label: `${label} kWh`,
        data: power[run].map((p) => ({ x: p.t, y: p.kwh })),
        borderColor: RUN_COLOR[run],
        backgroundColor: RUN_COLOR[run],
        borderDash: [8, 6],
        borderWidth: 2,
        pointRadius: 0,
        yAxisID: 'kwh',
      },
    ]),
  };
}

function chartOptions(axis: Axis | null, video: boolean): ChartOptions<'line'> {
  const font = { size: video ? 22 : 12 };
  const ink = '#c9d4e0';
  const grid = { color: 'rgba(255,255,255,0.08)' };
  return {
    animation: false,
    responsive: true,
    maintainAspectRatio: false,
    parsing: false,
    normalized: true,
    interaction: { mode: 'nearest', axis: 'x', intersect: false },
    plugins: {
      legend: { labels: { color: '#e6edf5', font, boxWidth: video ? 24 : 14, boxHeight: 3 } },
      tooltip: { callbacks: { title: (items) => (items[0] ? clockText(items[0].parsed.x ?? 0) : '') } },
    },
    scales: {
      x: {
        type: 'linear',
        min: axis?.[0],
        max: axis?.[1],
        ticks: { stepSize: video ? 10_800 : 3600, color: ink, font, callback: (v) => clockText(Number(v)) },
        grid,
      },
      kw: {
        position: 'left',
        beginAtZero: true,
        title: { display: true, text: 'kW', color: ink, font },
        ticks: { color: ink, font, maxTicksLimit: video ? 4 : 8 },
        grid,
      },
      kwh: {
        position: 'right',
        beginAtZero: true,
        title: { display: true, text: 'kWh', color: ink, font },
        ticks: { color: ink, font, maxTicksLimit: video ? 4 : 8 },
        grid: { display: false },
      },
    },
  };
}

export const EnergyChart = memo(function EnergyChart(props: {
  power: DashboardState['power'];
  latest: DashboardState['latest'];
  axis: Axis | null;
  video: boolean;
}) {
  const { power, latest, axis, video } = props;
  const data = useMemo(() => chartData(power), [power]);
  const options = useMemo(() => chartOptions(axis, video), [axis, video]);
  const populated = power.spotcool.length > 0 || power.baseline.length > 0;
  return (
    <Panel id="energy" title="HVAC power and energy" populated={populated}>
      {!populated ? (
        <Empty>Waiting for the plants' first measurements.</Empty>
      ) : (
        <div className="energy">
          <dl className="readouts">
            {RUNS.map(([run, label]) => {
              const m = latest[run];
              return (
                <div key={run} className={`readout readout-${run}`}>
                  <dt>{label}</dt>
                  <dd>
                    <span className="big">{fixed(m?.totalKw, 1)}</span> kW
                  </dd>
                  <dd>
                    <span className="big">{fixed(m?.energyKwh, 1)}</span> kWh
                  </dd>
                </div>
              );
            })}
          </dl>
          <div
            className="chart"
            role="img"
            aria-label={`Power and energy: SpotCool ${fixed(latest.spotcool?.energyKwh, 1)} kWh, baseline ${fixed(latest.baseline?.energyKwh, 1)} kWh so far`}
          >
            <Line data={data} options={options} />
          </div>
        </div>
      )}
    </Panel>
  );
});
